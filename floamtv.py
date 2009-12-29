#!/usr/bin/env python

# floamtv.py (Copyright 2008 Aaron Gyes)
# distributed under the GPLv3. See LICENSE

"""
Run without any arguments to use normally. For usage instructions, check out
http://aaron.gy/stuff/floamtv
"""

from __future__ import with_statement
import re, os, csv, yaml, sys, errno, atexit, pytz, shutil, resource, logging
import twisted, base64
from cStringIO import StringIO
from twisted.internet import reactor, task, defer
from pytz.reference import Local as localtz
from twisted.web import xmlrpc, server, client
from twisted.python import usage, log
from twisted.web.client import getPage
from urllib import urlencode
from xmlrpclib import ServerProxy
from datetime import datetime as dt, timedelta
from collections import defaultdict

dbpath = os.path.expanduser('~/.floamtvdb2')
configpath = os.path.expanduser('~/.floamtvconfig2')
pidfile = os.path.expanduser('~/.floamtvpid')
version = "internal"

tasks = {}
tr = re.compile(r"tvrage\.com/.*/([\d]{4,})")

defaults = {
   'config': { 'logfile': None,
               'max-connections': 3,
               'hellanzb-pass': 'changeme',
               'nzbclient': 'hella',
               'sabnzbd-host': 'localhost',
               'sabnzbd-port': 8080,
               'sabnzbd-apikey': 'changeme',
               'hellanzb-host': 'localhost',
               'newzbin-interval': 8,
               'tvrage-interval': 500,
               'retention': 100,
               'port': 19666,
               'bind': '',
               'sets': list() },
   
   'set': { 'shows': dict(),
            'timezone': 'US/Eastern',
            'rules': dict() },
   
   'rules': { 'min-megs': '',
              'max-megs': '',
              'groups': '',
              'query': '' }
}

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
      
      logging.info('Getting new data from TVRage.')
      def _check_for_brand_new_shows(_):
         def _start(x):
            if _firstrun:
               print "Quitting early to give you a chance to catch up."
               if reactor.running: reactor.stop()
            elif not tasks['newzbin'].running:
               tasks['newzbin'].start(60*config['newzbin-interval'])
         
         shows = set()
         tz = {}
         
         for a in sets:
            try:
               tz.update((s, a['timezone']) for s in a['shows'])
               shows.update(a['shows'])
            except TypeError:
               if reactor.running: reactor.stop()
               raise Exception, "You may have a colon in a show name. Shows " \
                                "with colons need to be enclosed in quotes."
               
         alreadyin = dict((t.title, t) for t in self.shows)
         newshows = []

         def new_show(info, timezone):
            if info:
               new_show = Show(info['wecallit'], timezone)
               dfrd = new_show.update(info)
               self.shows.append(new_show)
               return dfrd
         
         maxcon = min(10, int(config['max-connections']))
         ds = defer.DeferredSemaphore(tokens=maxcon)
         
         for show in shows.symmetric_difference(alreadyin):
            if show not in alreadyin:
               newshow = ds.run(tvrage_info, show, None)
               newshow.addCallbacks(new_show, tvrageerr, (tz[show],))
               newshow.addErrback(getpage_err)
               newshows.append(newshow)
            
            elif show not in shows:
               logging.info("Pruning %s" % alreadyin[show])
               self.shows.remove(alreadyin[show])
         
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
         ashow.addErrback(tvrageerr)
         ashow.addErrback(getpage_err)
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
             " ========\n\n"
      for ep in sorted(self._episodes(), key=lambda e: e.show):
         if ep.wanted or verbose:
            out += "  (%c) %s\n" % ('+' if ep.wanted else ' ', ep)
            out += "      (%s)\n\n" % relative_datetime(ep.airs)
      out += "  ( ) = unwanted, (+) = wanted"
      
      return out
   
   def look_on_newzbin(self, allow_probation=False):
      """
      Goes through and does a newzbin search for all wanted episodes. If we can
      resolve a newzbin id for a show we want, we enqueue it with hellanzb.
      
      We call episode.was_fake(sure=False) on episodes that are more than one
      hour early. This puts them in a state where we will only enqueue them if
      they are still on newzbin in two hours.
      
      If allow_probation is True, we enqueue episodes even if they're too early.
      """
      
      def _enqueue_new_stuff(results):
         for e in (ep for ep in self._episodes() if ep.wanted and ep.newzbinid):
            if e.airs and e.airs > dt.now(pytz.utc):
               if not allow_probation:
                  e.was_fake(sure=False)
            else:
               e.enqueue(allow_probation)
         self.save()
         
      logging.info('Looking for new shows on newzbin.')
      
      dfrds = []
      for ruleset in config['sets']:
         inset = [e for e in self._episodes() if e.show in ruleset['shows']]
         rdict = dict(defaults['rules'].items() + ruleset['rules'].items())
         dfrds.append(search_newzbin(inset, rdict))
      
      searches = defer.DeferredList(dfrds)
      searches.addCallback(_enqueue_new_stuff)
   
   def unwant(self, floamid):
      """
      Given a humanize()'d ID for an episode, set the episode not to enqueue
      when it becomes available. 'aired' will unwant all aired episodes.
      """
      if floamid == 'aired':
         out = ''
         for ep in self._episodes():
            if ep.wanted and ep.airs and ep.airs < dt.now(pytz.utc):
               out += self.unwant(humanize(ep.tvrageid)) + "\n"
         
         return out
      
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
      with open(dbpath + '~', 'w') as savefile:
         yaml.dump(self, savefile, indent=4, default_flow_style=False)
      
      shutil.move(dbpath + '~', dbpath)
   
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
   def __init__(self, title, timezone):
      self.episodes = []
      self.title = title
      self.timezone = timezone
      
   def _add_episode(self, info):
      "Given a dict with TVRage info, create a new Episode in self.episodes"
      if info['airs']:
            local = pytz.timezone(self.timezone)
            info['airs'] = local.localize(info['airs']).astimezone(pytz.utc)
      if info and info.get('tvrageid'):
         ep = Episode(**info)
   
         for gotit in (e for e in self.episodes if e.number == ep.number):
            attrs = ['title', 'airs', 'tvrageid']
            pert = lambda ep: [getattr(ep, attr) for attr in attrs]
            if ep and gotit and pert(ep) != pert(gotit):
               gotit.title = ep.title
               gotit.airs = ep.airs
               gotit.tvrageid = ep.tvrageid
               logging.info("Updated episode: %s" % gotit)
            break
         
         else:
            self.episodes.append(ep)
            logging.info("New episode: %s" % ep)

   def add(self, episode):
      """
      Given an episode-number string in the format used by TVRage, ('3x04' being
      season 3, episode 4), look up further information on this show and add it
      to self.episodes. If we already know about the show we won't do anything.
      """

      newepisode = tvrage_info(self.title, episode)
      newepisode.addCallbacks(self._add_episode, tvrageerr)
      newepisode.addErrback(getpage_err)

      return newepisode
   
   def update(self, rageinfo):
      """
      Given a dict with TVRage information on this show, try to add both the
      latest episode and the upcoming episode to this Show.
      """
      rcnt = filter(bool, [rageinfo['latest'], rageinfo['next']])
      cbs = [self.add(ep) for ep in rcnt]
      self.episodes = [e for e in self.episodes if e.number in rcnt or e.wanted
                       or len(rcnt) < 2]
      return defer.DeferredList([dfrd for dfrd in cbs if dfrd])
   
   def __repr__(self):
      return "<Show %s with episodes %s>" \
         % (self.title, (' and ').join(map(str, self.episodes)))
   
   def __str__(self):
      return self.title
   

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
            if 'hella' in config['nzbclient']:
            
               hella = ServerProxy("http://hellanzb:%s@%s:8760"
                        % (config['hellanzb-pass'], config['hellanzb-host']))
               hella.enqueuenewzbin(self.newzbinid)
            
            elif 'sab' in ['nzbclient']:
               def _handle_sab(result):
                  if 'error' in result:
                     logging.error("Unable to enqueue %s" % self)
                     logging.error("Reason: %s" % result)
                     return False
                     
               url = urlencode({'mode': 'addid',
                                'name': self.newzbinid,
                                'apikey': config['sabnzbd-apikey']})

               url = "http://%s:%s/sabnzbd/api?%s" % (config['sabnzbd-host'],
                                                      config['sabnzbd-port'], 
                                                      url)
               
               attempt = getPage(url, timeout=60)
               attempt.addCallback()
                  
      
         except:
            logging.error("Unable to enqueue %s" % self)
            return False
         
         else:
            logging.info("Enqueued %s" % self)
            self.wanted = False
            
   
   def educate(self):
      """
      After loading data from YAML, the datetimes will be naive.
      """
      try:
         self.airs = pytz.utc.localize(self.airs)
      except (AttributeError, ValueError):
         return
      
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
            logging.warning("%s was fake. Requeueing." % self)
            self.newzbinid = None
            self.wanted = True
         elif self.wanted:
            self.wanted = 'later'
            later = min(timedelta(hours=2), self.airs-dt.now(pytz.utc))
            latertime = (later + dt.now(localtz)).strftime("%I:%M %p %Z")
            
            logging.warning("%s is too early. Will try again at %s." % (self,
                                                                     latertime))
            reactor.callLater(later.seconds, self.enqueue, True)
   
   def __repr__(self):
      return "<Episode %s - %s - %s>" \
         % (self.show, self.number, self.title)
   
   def __str__(self):
      return "%s - %s - %s, [%s]" % (self.show, self.number, self.title,
                                     humanize(self.tvrageid))
      

class Options(usage.Options):
   def opt_version(self):
      print "floamtv %s" % version
      sys.exit()
   
   optFlags = [
      ['verbose',   'v', 'Show superfluous information when possible.'],
      ['status',    's', 'Show list of currently wanted episodes, and unwanted'\
                         ' episodes if verbose.'],
      ['daemonize', 'D', 'Causes floamtv to run as a daemon.'],
      ['shutdown',  'k',  'Quit the running floamtv.']
   ]
   optParameters = [
      ['unwant', None, None, 'Set an episode not to download when available'],
      ['rewant', None, None, 'Set an episode to download when available again'],
   ]

def am_server():
   "Returns True if there isn't another running instance of floamtv."
   pid = check_pid()
   if pid is None or pid == os.getpid():
      return True

def at_exit(showset):
   for e in showset._episodes():
      if e.wanted == 'later':
         e.wanted = True
      
   os.unlink(pidfile)
   showset.save()
   logging.info('Graceful exit.')

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

def daemonize():
   if os.fork() > 0: sys.exit() 
   os.chdir('/')
   os.setsid()
   os.umask(0)
   if os.fork() > 0: sys.exit()
   
   devnull = os.open(os.devnull, os.O_RDWR)
   for i in range(3):
      os.close(i)
      os.dup2(devnull, i)
   os.close(devnull)

def defaultize(D, U):
   U.update((k, type(D[k])(v)) for k,v in U.items() if k in D)   
   U.update((k,v) for k,v in D.items() if k not in U)
   U.update((k, defaultize(v, U[k])) for k,v in D.items() if type(v) is dict)
   
   return U

def getpage_err(err):
   return err.trap(twisted.internet.error.ConnectionLost)

def humanize(q):
   'Converts number to a base33 format, 0-9,a-z except i,l,o (look like digits)'
   if q < 0: raise ValueError, 'must supply a positive integer'
   letters = '0123456789abcdefghjkmnpqrstuvwxyz'
   converted = []
   while q != 0:
       q, r = divmod(q, 33)
       converted.insert(0, letters[r])
   return ('').join(converted)

def load():
   with open(dbpath, 'r') as savefile:
      ss = yaml.load(savefile)
      
      for e in ss._episodes():
         e.educate()
         
      return ss

def parse_tvrage(text, wecallit, is_episode):
   if text.startswith('No Show Results'):
      logging.warning("Show %r does not exist at TVRage." % wecallit)
      raise ValueError
   
   rage = defaultdict(lambda: None)
   
   for line in text.splitlines():
      part = line.split('@')
      rage[part[0]] = part[1].split('^') if '^' in part[1] else part[1]
   
   if is_episode and not rage.has_key('Episode URL'):
      raise ValueError
   
   clean = { 'wecallit': wecallit,
             'title':  rage['Show Name'],
             'next':   rage['Next Episode'] and rage['Next Episode'][0],
             'latest': rage['Latest Episode'] and rage['Latest Episode'][0] }

   if is_episode:
      clean['tvrageid'] = int(tr.findall(rage['Episode URL'])[-1])
      clean['number'] = rage['Episode Info'][0]
      clean['title'] = rage['Episode Info'][1]

      try:
         d = dt.strptime(rage['Episode Info'][2], "%d/%b/%Y")
         t = dt.strptime(re.findall("(\d\d.*)", rage['Airtime'])[0], "%I:%M %p")
         clean['airs'] = dt.combine(d.date(), t.time())
      except:
         clean['airs'] = None
   return clean

def relative_datetime(date):
   if date:
      date = date.astimezone(localtz)
      diff = date.date() - dt.now(localtz).date()

      if diff.days == 0:
         return "airs %s today"      % date.strftime("%I:%M %p")
      elif diff.days == -1:
         return "aired %s yesterday" % date.strftime("%I:%M %p")
      elif diff.days < -1:
         return "aired on %s"        % date.strftime("%m/%d/%Y")
      elif diff.days == 1:
         return "airs %s tomorrow"   % date.strftime("%I:%M %p")
      elif diff.days < 7:
         return "airs %s"            % date.strftime("%I:%M %p %A")
      else:
         return "airs on %s"         % date.strftime("%m/%d/%Y")
   else: return 'Unknown Airtime'

def search_newzbin(sepis, rdict):
   def _process_results(contents, sepis):
      rd = csv.reader(StringIO(contents))
      try:
         results = [(int((tr.findall(r[4]) or [0])[0]), int(r[1])) for r in rd]
         tvrageids = set(tvid for tvid, nbid in results)
      except IndexError:
         tvrageids = []
      
      for ep in sepis:
         if ep.airs and ep.newzbinid and ep.tvrageid not in tvrageids:
            if (ep.airs - dt.now(pytz.utc)).days > -7:
               ep.was_fake()
         
         if ep.tvrageid in tvrageids:
            for tvid, nbid in results:
               if tvid == ep.tvrageid:
                  ep.newzbinid = nbid
                  break
   
   query = urlencode({ 'searchaction': 'Search',
             'group': (' or ').join(rdict['groups']) if rdict['groups'] else '',
             'q': rdict['query'] or '',
             'category': 8,
             'u_completions': 1,
             'u_post_states': 3,
             'u_post_larger_than': rdict['min-megs'] or '',
             'u_post_smaller_than': rdict['max-megs'] or '',
             'q_url': (' or ').join([str(e.tvrageid) for e in sepis]),
             'sort': 'ps_edit_date',
             'order': 'desc',
             'u_post_results_amt': 999,
             'u_v3_retention': config['retention'] * 24 * 60 * 60,
             'fpn': 'p', 
             'feed': 'csv' })
   
   basicauth = base64.encodestring("%s:%s" % (config.get('newzbin-user'), 
                                               config.get('newzbin-password')))
   authheader = "Basic %s" % basicauth.strip()
   
   search = getPage("https://www.newzbin.com/search/?%s" % query, timeout=60,
                    headers={"Authorization": authheader})
   search.addCallback(_process_results, sepis)
   search.addErrback(getpage_err)
   return search

def set_up_logging(where):
   class ignr(logging.Filter):
      def filter(self, r): return r.levelname != "INFO"
   
   format = "%(asctime)s %(levelname)s: %(message)s" if where else "%(message)s"
   filename = os.path.expanduser(where) if where else None
   
   logging.basicConfig(level=logging.DEBUG, format=format,
                       datefmt="%b %d %H:%M:%S", filename=filename,
                       stream=sys.stdout)
   
   twistlog = logging.getLogger('twisted')
   twistlog.addFilter(ignr())
   try:
      observer = log.PythonLoggingObserver()
   except AttributeError:
      observer = log.DefaultObserver()
   observer.start()
   
def tvrageerr(err):
   return err.trap(ValueError)

def tvrage_info(show_name, episode):
   episode = episode or ''
   u = urlencode({'show': show_name, 'ep': episode})
   info = getPage("http://tvrage.com/quickinfo.php?%s" % u, timeout=60)
   info.addCallback(parse_tvrage, show_name, episode != '')
   return info

def main():
   set_up_logging(config['logfile'])
      
   if not am_server() and any(options.values()):
      showset = ServerProxy('http://localhost:19666/')
      
      if options['shutdown']:
         os.kill(check_pid(), 15)
   
      if options['status'] and check_pid():
         logging.info("Daemon is running.")
      
   else:
      if check_pid():
         return 'floamtv is already running.'

      if options['daemonize']:
         daemonize()
      
      with open(pidfile, "w") as f:
         f.write("%d" % os.getpid())
   
      if os.path.exists(dbpath):
         showset = load()
         first = False
      else:
         showset = Collection()
         first = True
      
   if options['unwant']:
      for show in options['unwant'].split(' '):
         logging.info(showset.unwant(show))
   
   elif options['rewant']:
      for show in options['rewant'].split(' '):
         logging.info(showset.rewant(show))
   
   elif options['status']:
      logging.info(showset.status(bool(options['verbose'])))
      
   elif am_server():
      atexit.register(at_exit, showset)
      
      tasks['tvrage'] = task.LoopingCall(showset.refresh, config['sets'], first)
      tasks['newzbin'] = task.LoopingCall(showset.look_on_newzbin)
      tasks['tvrage'].start(60 * config['tvrage-interval'])
      
      reactor.listenTCP(19666, server.Site(showset), interface=config['bind'])
      reactor.run()

if __name__ == '__main__':
   options = Options()
   options.parseOptions()
   
   try:
      with open(configpath, 'r') as configf:
         config = dict(defaults['config'].items() + yaml.load(configf).items())
         ### Come up with like apply_defaults function that goes through and
         ### hooks all the defaults up ahead of time.
      
   except IOError:
      sys.exit('You need to set up a config file first. See the docs.')
   
   sys.exit(main())