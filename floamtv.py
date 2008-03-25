#!/usr/bin/env python

# floamtv.py (Copyright 2008 Aaron Gyes)
# distributed under the GPLv3. See LICENSE

from __future__ import with_statement
import re, os, csv, yaml, time, sched, sys, signal, errno, atexit, threading
from urllib2 import urlopen
from urllib import urlencode
from optparse import OptionParser
from xmlrpclib import ServerProxy
from SimpleXMLRPCServer import SimpleXMLRPCServer
from datetime import datetime as dt, timedelta
from collections import defaultdict

dbpath = os.path.expanduser('~/.floamtvdb2')
configpath = os.path.expanduser('~/.floamtvconfig2')
pidfile = os.path.expanduser('~/.floamtvpid')

tr = re.compile(r"tvrage\.com/.*/([\d]{4,8})")
scheduler = sched.scheduler(time.time, time.sleep)

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

def rpc_or_here(func):
   def rpc_it(*a, **kw):
      return "Over the wire man!"
      #rpc = xmlrpc.ServerProxy('http://localhost:9061')
   
   def schedule_it(*a, **kw):
      scheduler.enter(0, 0, func, (a))
      return "Scheduled.."
   
   runningpid = checkpid()
   
   if runningpid is None:
      return func
   elif runningpid == os.getpid():
      return schedule_it
   else:
      return rpc_it

class Collection(yaml.YAMLObject):
   yaml_tag = '!Collection'
   
   def __init__(self, sets):
      self.shows = []
      self.refresh(sets)
      
   def serveviaxmlrpc(self):
      server = SimpleXMLRPCServer(('localhost', 9061))
      server.register_introspection_functions()
      server.register_function(self.unwant, 'unwant')
      server.register_function(self.rewant, 'rewant')
      server.serve_forever()
   
   def refresh(self, sets):
      print 'Getting new data from TVRage.'
      for show in self.shows:
         show.update()
      
      shows = set()
      for aset in sets:
         shows.update(set(aset['shows']))
      
      alreadyin = [t.title for t in self.shows]
      self.shows += [Show(s) for s in shows if s not in alreadyin]
      
      self.save()
      scheduler.enter(60*60*2, 1, self.refresh, (sets,))
   
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
      print 'Looking for new shows on newzbin.'
      for ruleset in config['sets']:
         inset = [e for e in self._episodes() if e.show in ruleset['shows']]
         search_newzbin(inset, ruleset['rules'])
      
      for ep in self._episodes():
         if ep.wanted and ep.newzbinid:
            if ep.airs and (ep.airs-dt.now()) > timedelta(hours=3) and not iffy:
               ep.was_fake(sure=False)
            else:
               ep.enqueue(allowiffy=iffy)
               if ep.wanted:
                  print "What fuck what"
               
      self.save()
      scheduler.enter(60*8, 1, self.look_on_newzbin, (False,))
   
   @rpc_or_here
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
   
   @rpc_or_here
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
   def __init__(self, show):
      info = tvrage_info(show)
      self.title = show
      self.episodes = []
      self.update(info)
   
   def add(self, episode):
      if episode and episode not in (e.number for e in self.episodes):
         ep = Episode(self.title, episode)
         if ep.tvrageid:
            self.episodes.append(ep)
            print "New episode %s" % ep
   
   def update(self, rageinfo=None):
      if not rageinfo:
         rageinfo = tvrage_info(self.title)
      rcnt = filter(bool, [rageinfo['latest'], rageinfo['next']])
      
      for ep in rcnt:
         self.add(ep)
      
      self.episodes = [e for e in self.episodes if e.number in rcnt or e.wanted]
   
   def __repr__(self):
      return "<Show %s with episodes %s>" \
         % (self.title, ' and '.join(map(str, self.episodes)))
   


class Episode(yaml.YAMLObject):
   yaml_tag = '!Episode'
   def __init__(self, show, number):
      info = tvrage_info(show, number)
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
         scheduler.enter(60*60*2, 1, self.enqueue, (True,))
   
   def __repr__(self):
      return "<Episode %s - %s - %s>" \
         % (self.show, self.number, self.title)
   
   def __str__(self):
      return "%s - %s - %s, [%s]" % (self.show, self.number, self.title,
                                     humanize(self.tvrageid))
   

def tvrage_info(show_name, episode=''):
   rage = clean = defaultdict(lambda: None)
   
   u = urlencode({'show': show_name, 'ep': episode})
   showinfo = urlopen("http://tvrage.com/quickinfo.php?%s" % u)
   result = showinfo.read()
   
   if result.startswith('No Show Results'):
      raise Exception, "Show %s does not exist at tvrage." % show_name
   
   for line in result.splitlines():
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
   
   showinfo.close()
   return clean

def search_newzbin(sepis, rdict):
   '''Search newzbin for a list of episodes sepis and rules rdict. Episodes are
      updated with newzbinids.'''
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
   
   search = urlopen("https://v3.newzbin.com/search/?%s" % query)
   results = dict((int(tr.findall(r[4])[0]), int(r[1]))
              for r in csv.reader(search))
   search.close()
   
   for ep in sepis:
      if ep.airs and ep.newzbinid and ep.tvrageid not in results:
         if (ep.airs - dt.now()).days > -7:
            ep.was_fake()
      
      if ep.tvrageid in results:
         ep.newzbinid = results[ep.tvrageid]

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

      rpc = threading.Thread(target=showset.serveviaxmlrpc)
      rpc.setDaemon(True)
      rpc.start()
      
      scheduler.enter(0, 1, showset.refresh, (config['sets'],))
      scheduler.enter(0, 1, showset.look_on_newzbin, (True,))
      scheduler.run()
      

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