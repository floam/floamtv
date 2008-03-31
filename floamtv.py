#!/usr/bin/env python

# floamtv.py (Copyright 2008 Aaron Gyes)
# distributed under the GPLv3. See LICENSE

# Remaining bugs
#  - Implement attributes for fucks sake!


from __future__ import with_statement
import re, os, csv, yaml, time, sys, errno, atexit
from cStringIO import StringIO
from twisted.internet import reactor, task, defer
from twisted.web import xmlrpc, server
from twisted.web.client import getPage
from urllib import urlencode
from optparse import OptionParser
from xmlrpclib import ServerProxy
from datetime import datetime as dt, timedelta as td
from collections import defaultdict

dbpath = os.path.expanduser('~/.floamtvdb2')
configpath = os.path.expanduser('~/.floamtvconfig2')
pidfile = os.path.expanduser('~/.floamtvpid')

tasks = {}
tr = re.compile(r"tvrage\.com/.*/([\d]{4,8})")

class Collection(yaml.YAMLObject, xmlrpc.XMLRPC):
   """
   The Collection object holds all our Shows. It also provides functions to
   do things to our collection of Shows.
   
   An individual episode can be chosen through Collection[id], where id is a
   humanize()'d version of the TVRage ID.
   """
   
   yaml_tag = '!Collection'
   allowNone = False
   def __init__(self, sets=None):
      self.shows = []
      if sets:
         self.refresh(sets)
   
   def refresh(self, sets, _firstrun=False):
      """
      Populate this Collection with new show information from TVRage. Existing
      Shows have new episodes added to them and the rest are updated with new 
      episodes. sets looks like this:
      
      [
         { 'rules': { ... }, 'shows': ['Show Name 1', 'Show Name 2'] },
         { 'rules': { ... }, 'shows': [ ... ] },
         ...
      ]
      
      Rules don't affect this step at all.
      """
      
      print 'Getting new data from TVRage.'
      def _check_for_brand_new_shows(_):
         def _start(x):
            if _firstrun:
               print "Quitting early to give you a chance to catch up."
               reactor.stop()
            elif not tasks['newzbin'].running:
               tasks['newzbin'].start(60*config['newzbin-interval'])
         
         shows = set()
         for aset in sets:
            shows.update(set(aset['shows']))
         
         alreadyin = [t.title for t in self.shows]
         newshows = []
         
         def new_show(info):
            new_show = Show(info['wecallit'])
            dfrd = new_show.update(info)
            self.shows.append(new_show)
            return dfrd
         
         for s in (z for z in shows if z not in alreadyin):
            newshow = tvrage_info(s, None)
            newshow.addCallback(new_show)
            newshows.append(newshow)
            
         ns = defer.DeferredList(newshows)
         ns.addCallback(_start)
         ns.addCallback(lambda _: self.save())
         
         return ns
            
      if tasks.has_key('newzbin') and tasks['newzbin'].running:
         tasks['newzbin'].stop()
      
      pageinfos = []
      for show in self.shows:
         ashow = tvrage_info(show.title, None)
         ashow.addCallback(show.update)
         pageinfos.append(ashow)
         
      pageinfos = defer.DeferredList(pageinfos)
      pageinfos.addCallback(_check_for_brand_new_shows)
      
      return pageinfos
   
   def status(self, verbose):
      """
      Returns a pretty listing of shows we know about. If verbose is True,
      all shows we know about are included, else only wanted shows.
      """
      
      out = "\n Episodes\n"\
             " ========\n"
      for ep in sorted(self._episodes(), key=lambda e: e.show):
         if ep.wanted or verbose:
            w = '+' if e.wanted else '-'
            out += "  (%s) %s\n\t(%s)\n" % (w, e, relative_datetime(e.airs))
      
      return out
   
   def look_on_newzbin(self, allow_probation=False):
      """
      Goes through and does a newzbin search for all wanted episodes. If we can
      resolve a newzbin id for a show we want, we enqueue it with hellanzb.
      
      We call episode.was_fake(sure=False) on episodes that are more than three
      hours early. This puts them in a state where we will only enqueue them if
      they are still on newzbin in two hours.
      
      If allow_probation is True, we enqueue episodes even if they're too early.
      """
      
      def _enqueue_new_stuff(results):
         for e in (ep for ep in self._episodes() if ep.wanted and ep.newzbinid):
            if e.airs and e.airs-dt.now() > td(hours=3) and not allow_probation:
               e.was_fake(sure=False)
            else:
               e.enqueue(allow_probation)
         self.save()
         
      print 'Looking for new shows on newzbin.'
      
      dfrds = []
      for ruleset in config['sets']:
         inset = [e for e in self._episodes() if e.show in ruleset['shows']]
         dfrds.append(search_newzbin(inset, ruleset['rules']))
      
      searches = defer.DeferredList(dfrds)
      searches.addCallback(_enqueue_new_stuff)
   
   def unwant(self, floamid):
      """
      Given a humanize()'d ID for an episode, set the episode not to enqueue
      when it becomes available.
      """
      try:
         if self[floamid].wanted:
            self[floamid].wanted = False
            self[floamid].newzbinid = None
            self.save()
            return "Will not download %s when available." % self[floamid]
         else:
            return "Error: %s is already unwanted" % self[floamid]
      except KeyError:
         return "Error: %s is not a valid id." % floamid
   
   def rewant(self, floamid):
      """
      Given a humanize()'d ID for an episode, set the episode to enqueue when it
      becomes available.
      """
      try:
         if not self[floamid].wanted:
            self[floamid].wanted = True
            self[floamid].newzbinid = None
            self.save()
            return "Will download %s when available." % self[floamid]
         else:
            return "Error: %s is already wanted" % self[floamid]
      except KeyError:
         return "Error: %s is not a valid id." % floamid
         
   def save(self):
      """
      Write the entire Collection to disk (at global dbpath) in YAML format.
      """
      with open(dbpath, 'w') as savefile:
         yaml.dump (self, savefile, indent=4, default_flow_style=False)
   
   def _episodes(self):
      for show in self.shows:
         for episode in show.episodes:
            yield episode
   
   def __getitem__(self, item):
      for episode in self._episodes():
         if humanize(episode.tvrageid) == item:
            return episode
      else:
         raise KeyError, "No episode with id %s" % item
   
   xmlrpc_status = status
   xmlrpc_unwant = unwant
   xmlrpc_rewant = rewant

class Show(yaml.YAMLObject):
   """
   A Show represents a television show, which has Episodes as children. Takes
   only one argument, the show title. Does not automatically populate itself,
   you will need to .update() it with information.
   """
   
   yaml_tag = '!Show'
   def __init__(self, title):
      self.episodes = []
      self.title = title
   
   def _add_episode(self, infodict):
      "Given a dict with TVRage info, create a new Episode in self.episodes"
      
      if infodict['tvrageid']:
         ep = Episode(**infodict)
         if ep.tvrageid:
            self.episodes.append(ep)
            print "New episode: %s" % ep
   
   def add(self, episode):
      """
      Given an episode-number string in the format used by TVRage, ('3x04' being
      season 3, episode 4), look up further information on this show and add it
      to self.episodes. If we already know about the show we won't do anything.
      """
      
      if episode and episode not in (e.number for e in self.episodes):
         newepisode = tvrage_info(self.title, episode)
         newepisode.addCallback(self._add_episode)
         return newepisode
   
   def update(self, rageinfo):
      """
      Given a dict with TVRage information on this show, try to add both the
      latest episode and the upcoming episode to this Show.
      """
      rcnt = filter(bool, [rageinfo['latest'], rageinfo['next']])
      
      cbs = [self.add(ep) for ep in rcnt]
      self.episodes = [e for e in self.episodes if e.number in rcnt or e.wanted]
      
      return defer.DeferredList([dfrd for dfrd in cbs if dfrd])
   
   def __repr__(self):
      return "<Show %s with episodes %s>" \
         % (self.title, ' and '.join(map(str, self.episodes)))
   

class Episode(yaml.YAMLObject):
   """
   Represents a television episode, takes a bunch of arguments:
   
    wecallit: Show's title, however not the real name, but what we searched for.
    title:    Episode title.
    tvrageid: Integer ID number used by TVRage to identify an episode.
    airs:     datetime.datetime() or None if unknown airtime.
    number:   Something like '3x05', season 3 episode 5.
   """
   
   yaml_tag = '!Episode'
   def __init__(self, wecallit, number, title, tvrageid, airs, *a, **kw):
      self.show = wecallit
      self.number = number
      self.title = title
      self.tvrageid = tvrageid
      self.airs = airs
      self.newzbinid = None
      self.wanted = True
   
   def enqueue(self, allow_probation=False):
      """
      Enqueue episodes that have a Newzbin ID resolved with Hellanzb. If
      allow_probation is True, don't exclude shows that are on probation.
      """
      
      if self.newzbinid and self.wanted != 'later' or allow_probation:
         try:
            hella = ServerProxy("http://hellanzb:%s@%s:8760"
                           % (config['hellanzb-pass'], config['hellanzb-host']))
            hella.enqueuenewzbin(self.newzbinid)
         except:
            print "Unable to enqueue %s" % self
            return False
                     
         print "Enqueued %s" % self
         self.wanted = False
   
   def was_fake(self, sure=True):
      """
      Call this if we find out a newzbinid for a show is actually pointing at a
      fake post. It'll be put to the same state it was at before we found a
      newzbinid for it. Call with sure = False if the post is suspect prior to
      downloading -- this will put it on 'probation' for a couple hours and only
      download it if it isn't deleted from Newzbin in the interim.
      """
      if self.wanted != 'later':
         if sure:
            print "%s was fake. Requeueing." % self
            self.newzbinid = None
            self.wanted = True
         elif self.wanted:
            print "%s is too early. Will confirm in a couple hours." % self
            self.wanted = 'later'
            reactor.callLater(60*60*2, self.enqueue, True)
   
   def __repr__(self):
      return "<Episode %s - %s - %s>" \
         % (self.show, self.number, self.title)
   
   def __str__(self):
      return "%s - %s - %s, [%s]" % (self.show, self.number, self.title,
                                     humanize(self.tvrageid))


def am_server():
   "Returns True if there isn't another running instance of floamtv."
   pid = check_pid()
   if pid is None or pid == os.getpid():
      return True

def at_exit(showset):
   print "Cleaning shit up."
   os.unlink(pidfile)
   showset.save()

def check_pid():
   if os.path.exists(pidfile):
      with open(pidfile) as f:
         pid = f.read()
      
      try:
         os.kill(int(pid), 0)
      except os.error, err:
         if err.errno == errno.ESRCH:
            os.unlink(pidfile)
      else:
         return int(pid)

def humanize(q):
   'Converts number to a base33 format, 0-9,a-z except i,l,o (look like digits)'
   if q < 0: raise ValueError, 'must supply a positive integer'
   letters = '0123456789abcdefghjkmnpqrstuvwxyz'
   converted = []
   while q != 0:
       q, r = divmod(q, 33)
       converted.insert(0, letters[r])
   return ''.join(converted)

def load():
   with open(dbpath, 'r') as savefile:
      return yaml.load(savefile)

def parse_tvrage(text, wecallit):
   rage = defaultdict(lambda: None)
   clean = rage.copy()
   
   if text.startswith('No Show Results'):
      raise Exception, "Show %s does not exist at tvrage." % show_name
   
   for line in text.splitlines():
      part = line.split('@')
      rage[part[0]] = part[1].split('^') if '^' in part[1] else part[1]
   
   clean.update({
      'wecallit': wecallit,
      'title': rage['Show Name'],
      'next': rage['Next Episode'][0] if rage['Next Episode'] else None,
      'latest': rage['Latest Episode'][0] if rage['Latest Episode'] else None })
   
   if rage['Episode URL']:
      clean.update({ 'tvrageid': int(tr.findall(rage['Episode URL'])[-1]),
                     'number': rage['Episode Info'][0],
                     'title': rage['Episode Info'][1],
                     'airs': "%s; %s" % (rage['Episode Info'][2],
                                                              rage['Airtime'])})
   try:
      clean['airs'] = dt.strptime(clean['airs'], "%d/%b/%Y; %A, %I:%M %p")
   except:
      clean['airs'] = None
   
   return clean

def relative_datetime(date):
   # taken from <http://odondo.wordpress.com/2007/07/05/>, thanks!
   if date:
      diff = date.date() - dt.now().date()

      if diff.days == 0:
         return "airs %s today" % date.strftime("%I:%M %p")
      elif diff.days == -1:
         return "aired %s yesterday" % date.strftime("%I:%M %p")
      elif diff.days < -1:
         return "aired on %s" % date.strftime("%m/%d/%Y")
      elif diff.days == 1:
         return "airs %s tomorrow" % date.strftime("%I:%M %p")
      elif diff.days > -7:
         return "airs %s" % date.strftime("%I:%M %p %A")
      else:
         return "airs on %s" % date.strftime("%m/%d/%Y")
   else: return 'Unknown Airtime'

def search_newzbin(sepis, rdict):
   def _process_results(contents, sepis):
      reader = csv.reader(StringIO(contents))

      results = [(int(tr.findall(r[4])[0]), int(r[1])) for r in reader]
      tvrageids = set(tvid for tvid, nbid in results)
      
      for ep in sepis:
         if ep.airs and ep.newzbinid and ep.tvrageid not in tvrageids:
            if (ep.airs - dt.now()).days > -7:
               print ep.tvrageid in tvrageids
               ep.was_fake()
         
         if ep.tvrageid in tvrageids:
            for tvid, nbid in results:
               if tvid == ep.tvrageid:
                  ep.newzbinid = nbid
                  break
   
   rules = defaultdict(lambda: '')
   rules.update(rdict)
   query = urlencode({ 'searchaction': 'Search',
             'group': rules['group'],
             'q': rules['query'],
             'category': 8,
             'u_completions': 9,
             'u_post_states': 2,
             'u_post_larger_than': rules['min-megs'],
             'u_post_smaller_than': rules['max-megs'],
             'q_url': ' or '.join([str(e.tvrageid) for e in sepis]),
             'sort': 'ps_edit_date',
             'order': 'desc',
             'u_post_results_amt': 999,
             'feed': 'csv' })
   
   search = getPage("https://v3.newzbin.com/search/?%s" % query)
   search.addCallback(_process_results, sepis)
   return search

def tvrage_info(show_name, episode):
   episode = episode or ''
   u = urlencode({'show': show_name, 'ep': episode})
   info = getPage("http://tvrage.com/quickinfo.php?%s" % u)
   info.addCallback(parse_tvrage, show_name)
   return info

def main():
   if not am_server():
      showset = ServerProxy('http://localhost:19666/')
   else:
      if check_pid():
         raise SystemExit, 'floamtv is already running.'

      with open(pidfile, "w") as f:
         f.write("%d" % os.getpid())
   
      if os.path.exists(dbpath):
         showset = load()
         first = False
      else:
         showset = Collection()
         first = True
      
   if options.unwant:
      for show in options.unwant.split(' '):
         print showset.unwant(show)
   
   elif options.rewant:
      for show in options.rewant.split(' '):
         print showset.rewant(show)
   
   elif options.status:
      print showset.status(bool(options.verbose))
      
   elif am_server():
      atexit.register(at_exit, showset)
      
      tasks['tvrage'] = task.LoopingCall(showset.refresh, config['sets'], first)
      tasks['newzbin'] = task.LoopingCall(showset.look_on_newzbin, True)
      
      tasks['tvrage'].start(60 * config['tvrage-interval'])
      
      reactor.listenTCP(19666, server.Site(showset))
      reactor.run()

if __name__ == '__main__':
   try:
      with open(configpath, 'r') as configuration:
         config = yaml.load(configuration)
   except IOError:
      print 'You need to set up a config file first. See the docs.'
      sys.exit()
   
   parser = OptionParser()
   parser.add_option('-u', '--update', action='store_true', dest='updatedb',
                     help='update show information from TVRage.')
   parser.add_option('--unwant', dest='unwant',
                     help='Set an episode to not download when available.')
   parser.add_option('--rewant', dest='rewant',
                     help='Set previously unwanted episode to download when ' \
                     'available.')
   parser.add_option('-s', '--status', dest='status', action='store_true',
                     help='Print information and status stuff.')
   parser.add_option('-v', '--verbose', dest='verbose', action='store_true',
                     help='Print more information than normal.')
   options, args = parser.parse_args()
   
   main()