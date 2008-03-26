#!/usr/bin/env python

# floamtv.py (Copyright 2008 Aaron Gyes)
# distributed under the GPLv3. See LICENSE

from __future__ import with_statement
import re, os, csv, yaml, time, sys, errno, atexit, threading
from twisted.internet import reactor, task, defer
from twisted.web.client import getPage
from urllib2 import urlopen
from urllib import urlencode
from optparse import OptionParser
from xmlrpclib import ServerProxy
from datetime import datetime as dt, timedelta
from collections import defaultdict

dbpath = os.path.expanduser('~/.floamtvdb2')
configpath = os.path.expanduser('~/.floamtvconfig2')
pidfile = os.path.expanduser('~/.floamtvpid')

tr = re.compile(r"tvrage\.com/.*/([\d]{4,8})")

try:
   with open(configpath, 'r') as configuration:
      config = yaml.load(configuration)
except IOError:
   print 'You need to set up a config file first. See the docs.'


def checkpid():
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

if checkpid():
   raise SystemExit, 'floamtv is already running.'

with open(pidfile, "w") as f:
   f.write("%d" % os.getpid())

class Collection(yaml.YAMLObject):
   yaml_tag = '!Collection'
   
   def __init__(self, sets):
      self.shows = []
      self.refresh(sets)
      
   def refresh(self, sets):
      print 'Getting new data from TVRage.'
      for show in self.shows:
         show.update(tvrage_info(show.title))
      
      shows = set()
      for aset in sets:
         shows.update(set(aset['shows']))
      
      alreadyin = [t.title for t in self.shows]
      self.shows += [Show(s, tvrage_info(s)) for s in shows if s not in alreadyin]
      
      self.save()
   
   def print_status(self):
      def format(e): return "  %s\n    (%s)\n" % (e, relative_datetime(e.airs))
      def show(e): return e.show
      
      print "\n Episodes we want\n"\
            " ================\n"
      for ep in sorted(self._episodes(), key=show):
         if ep.wanted:
            print format(ep)
      
      if options.verbose:
         print "\n Unwanted Episodes\n"\
               " ===================\n"
         for ep in sorted(self._episodes(), key=show):
            if not ep.wanted:
               print format(ep)
   
   def look_on_newzbin(self, iffy=False):
      def enqueue_new_stuff(results):
         for ep in self._episodes():
            if ep.wanted and ep.newzbinid:
               if ep.airs and (ep.airs-dt.now()) > timedelta(hours=3) and not iffy:
                  ep.was_fake(sure=False)
               else:
                  ep.enqueue(allowiffy=iffy)
         self.save()
         
      print 'Looking for new shows on newzbin.'
      
      dfrds = []
      for ruleset in config['sets']:
         inset = [e for e in self._episodes() if e.show in ruleset['shows']]
         dfrds.append(search_newzbin(inset, ruleset['rules']))
      
      searches = defer.DeferredList(dfrds)
      searches.addCallback(enqueue_new_stuff)
   
   def unwant(self, item):
      try:
         if self[item].wanted:
            self[item].wanted = False
            self.save()
            print "Will not download %s when available." % self[item]
         else:
            print "Error: %s is already unwanted" % self[item]
      except KeyError:
         print "Error: %s is not a valid id." % item
   
   def rewant(self, item):
      try:
         if not self[item].wanted:
            self[item].wanted = True
            self.save()
            print "Will download %s when available." % self[item]
         else:
            print "Error: %s is already wanted" % self[item]
      except KeyError:
         print "Error: %s is not a valid id." % item
         
   def save(self):
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

class Show(yaml.YAMLObject):
   yaml_tag = '!Show'
   def __init__(self, show, info):
      self.title = show
      self.episodes = []
      self.update(info)
   
   def add(self, episode):
      if episode and episode not in (e.number for e in self.episodes):
         ep = Episode(self.title, tvrage_info(self.title, episode))
         if ep.tvrageid:
            self.episodes.append(ep)
            print "New episode %s" % ep
   
   def update(self, rageinfo):
      rcnt = filter(bool, [rageinfo['latest'], rageinfo['next']])
      
      for ep in rcnt:
         self.add(ep)
      
      self.episodes = [e for e in self.episodes if e.number in rcnt or e.wanted]
   
   def __repr__(self):
      return "<Show %s with episodes %s>" \
         % (self.title, ' and '.join(map(str, self.episodes)))
   


class Episode(yaml.YAMLObject):
   yaml_tag = '!Episode'
   def __init__(self, show, info):
      self.show = show
      self.number = info['number']
      self.title = info['title']
      self.tvrageid = info['tvrageid']
      self.airs = info['airs']
      self.newzbinid = None
      self.wanted = True
   
   def enqueue(self, allowiffy=False):
      if self.newzbinid and self.wanted != 'later' or allowiffy:
         try:
            print "Telling to enqueue..."
            hella = ServerProxy("http://hellanzb:%s@%s:8760"
                           % (config['hellanzb-pass'], config['hellanzb-host']))
            hella.enqueuenewzbin(self.newzbinid)
         except:
            print "Unable to enqueue %s" % self
            return False
                     
         print "Enqueued %s" % self
         self.wanted = False
   
   def was_fake(self, sure=True):
      if sure:
         if self.wanted != 'later':
            print "%s was fake. Requeueing." % self
         self.newzbinid = None
         self.wanted = True
      elif self.wanted != 'later':
         print "%s is too early. Will confirm in a couple hours." % self
         self.wanted = 'later'
         reactor.callLater(60*60*2, self.enqueue, True)
   
   def __repr__(self):
      return "<Episode %s - %s - %s>" \
         % (self.show, self.number, self.title)
   
   def __str__(self):
      return "%s - %s - %s, [%s]" % (self.show, self.number, self.title,
                                     humanize(self.tvrageid))
   

def tvrage_info(show_name, episode=''):
   u = urlencode({'show': show_name, 'ep': episode})
   showinfo = urlopen("http://tvrage.com/quickinfo.php?%s" % u)
   result = parse_tvrage(showinfo.read())
   showinfo.close()
   return result


def parse_tvrage(text):
   rage = clean = defaultdict(lambda: None)
   if text.startswith('No Show Results'):
      raise Exception, "Show %s does not exist at tvrage." % show_name
   
   for line in text.splitlines():
      part = line.split('@')
      rage[part[0]] = part[1].split('^') if '^' in part[1] else part[1]
   
   clean.update({
      'title': rage['Show Name'],
      'next': rage['Next Episode'][0] if rage['Next Episode'] else None,
      'latest': rage['Latest Episode'][0] if rage['Latest Episode'] else None
   })
   
   if rage['Episode URL']:
      clean.update({
         'tvrageid': int(tr.findall(rage['Episode URL'])[-1]),
         'number': rage['Episode Info'][0],
         'title': rage['Episode Info'][1],
         'airs': "%s; %s" % (rage['Episode Info'][2], rage['Airtime'])
      })
   
   try:
      clean['airs'] = dt.strptime(clean['airs'], "%d/%b/%Y; %A, %I:%M %p")
   except (ValueError, TypeError):
      clean['airs'] = None
   
   return clean

def search_newzbin(sepis, rdict):
   def process_newzbin_results(contents, sepis):
      results = dict((int(tr.findall(r[4])[0]), int(r[1]))
                 for r in csv.reader(contents))
      for ep in sepis:
         if ep.airs and ep.newzbinid and ep.tvrageid not in results:
            if (ep.airs - dt.now()).days > -7:
               ep.was_fake()

         if ep.tvrageid in results:
            ep.newzbinid = results[ep.tvrageid]
      
   rules = defaultdict(lambda: '')
   rules.update(rdict)
   query = urlencode({ 'searchaction': 'Search',
             'group': rules['group'],
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
   search.addCallback(process_newzbin_results, (sepis,))
   return search

def humanize(q):
   'Converts number to a base33 format, 0-9,a-z except i,l,o (look like digits)'
   if q < 0: raise ValueError, 'must supply a positive integer'
   letters = '0123456789abcdefghjkmnpqrstuvwxyz'
   converted = []
   while q != 0:
       q, r = divmod(q, 33)
       converted.insert(0, letters[r])
   return ''.join(converted)

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

def load():
   with open(dbpath, 'r') as savefile:
      return yaml.load(savefile)

def cleanup(showset):
   print "Cleaning shit up."
   os.unlink(pidfile)
   showset.save()

def main():
   if os.path.exists(dbpath):
      showset = load()
   else:
      showset = Collection(config['sets'])
      print "This was the first run, exiting. You'll probably want to --unwant"\
            " any episodes you don't want to be fetched next time."
      return

   if options.unwant:
      for show in options.unwant.split(' '):
         print showset.unwant(show)
      
   elif options.rewant:
      for show in options.rewant.split(' '):
         print showset.rewant(show)

   elif options.status:
      showset.print_status()

   else:
      atexit.register(cleanup, (showset))
      
      task.LoopingCall(showset.refresh, config['sets']).start(60*5)
      task.LoopingCall(showset.look_on_newzbin, True).start(60*60*3)
      
      reactor.run()
      
if __name__ == '__main__':
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