#!/usr/bin/env python

# floamtv.py (Copyright 2008 Aaron Gyes)
# distributed under the GPLv3. See LICENSE

# Fill up the shows database with public domain TV shows that TVRage knows
# about. Give it some rules, and it'll return newzbin report IDs of what you
# want. Use cron to update the database (-u) once or twice per day and search
# newzbin with it as often as you like. Shows are considered downloaded once
# it has told you about them. Use --pretend for a dry-run. --help for more
# information.


'FOR ME: old_episodes | (new_episodes - old_episodes)'

from __future__ import with_statement
import sys, re, os.path, csv, yaml
from urllib2 import urlopen
from urllib import urlencode
from optparse import OptionParser
from xmlrpclib import ServerProxy
from datetime import datetime as dt
from collections import defaultdict

dbpath = os.path.expanduser('~/.floamtvdb2')
configpath = os.path.expanduser('~/.floamtvconfig2')

if os.path.exists(configpath):
   with open(configpath, 'r') as configuration:
      config = yaml.load(configuration)
else:
   print "You need to set up a config file first. See the docs."
   sys.exit()

tr = re.compile(r"tvrage\.com/.*/([\d]{4,8})")

parser = OptionParser()
parser.add_option('-r', '--run', action='store_true', dest='run',
                  help='search newzbin and enqueue episodes that are ready.')
parser.add_option('-u', '--update', action='store_true', dest='updatedb',
                  help='update show information from TVRage.')
parser.add_option('-p', '--pretend', action='store_true', dest='pretend',
                  help="don't actually do anything -- pretend to.")
parser.add_option('-s', '--status', dest='status', action='store_true',
                  help="Print information and status stuff.")             
parser.add_option('-d', '--delete', action='append', dest='delete',
                  help='manually remove episode from waitqueue', metavar='ID')
options, args = parser.parse_args()

class Collection(yaml.YAMLObject):
   yaml_tag = "!Collection"
   
   def __init__(self, sets):
      self.shows = []
      self.refresh(sets)
   
   def enqueue(self, specific=None):
      if not specific:
         for show in self.shows:
            for episode in show.episodes:
               if episode.wanted and episode.newzbinid:
                  episode.enqueue()
      else:
         self[specific].enqueue()
   
   def refresh(self, sets):
      if options.updatedb:
         for show in self.shows:
            show.update()
      
      shows = set()
      for aset in sets:
         shows.update(set(aset['shows']))
      self.shows += [Show(s) for s in shows if s not in self]
   
   def report(self, type='all'):
      print "\n Episodes we want\n"\
            " ================\n"
      for ep in self.wanted():
         print "  %s - %s - %s" % (ep.show, ep.title, ep.number)
         print "   airs %s\n" % relative_datetime(ep.airs)
   
   def episodes(self):
      for show in self.shows:
         for episode in show.episodes:
            yield episode
   
   def __contains__(self, cont):
      if self.__getitem__(cont) or cont in self.shows:
         return True
      else:
         return False
   
   def __getitem__(self, item):
      for show in (s for s in self.shows if item in s.title):
         return show

class Show(yaml.YAMLObject):
   yaml_tag = '!Show'
   def __init__(self, show):
      info = tvrage_info(show)
      self.title = show
      self.episodes = []
      self.update(info)
      
   def add(self, episode):
      if episode and episode not in (e.number for e in self.episodes):
         print "New episode %s %s" % (self.title, episode)
         self.episodes.append(Episode(self.title, episode))
   
   def update(self, rageinfo=None):
      if not rageinfo:
         rageinfo = tvrage_info(self.title)
      recent = filter(bool, [rageinfo['latest'], rageinfo['next']])
      
      for ep in recent:
         self.add(ep)
         
      self.episodes = [e for e in self.episodes if e.number in recent]
   
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
   
   def enqueue(self):
      if self.newzbinid:
         hella = ServerProxy("http://hellanzb:%s@localhost:8760"
                                 % config['hellapass'])
         log = hella.enqueuenewzbin(self.newzbinid)['log_entries'][-1]['INFO']
         if str(self.newzbinid) in log:
            print "Enqueued %s - %s" % (self.show, self.number)
            self.wanted = False
      else:
         raise Exception, "Can't enqueue episode not on newzbin."
   
   def wasfake(self, sure=True):
      if sure:
         print "%s - %s was fake. Requeueing." % (self.show, self.number)
         self.newzbinid = None
         self.wanted = True
      else:
         # The post was fishy.
         # We will check if the show is still on newzbin in 1.5 hours, and
         # then enqueue it.
         pass
   
   def __repr__(self):
      return "<Episode %s - %s - %s>" \
         % (self.show, self.number, self.title)
   
def relative_datetime(date):
   # taken from <http://odondo.wordpress.com/2007/07/05/>
   if date:
      diff = date.date() - dt.now().date()
   
      if diff.days == 0:
         return 'at ' + date.strftime("%I:%M %p")
      elif diff.days == -1:
         return 'at ' + date.strftime("%I:%M %p") + ' yesterday'
      elif diff.days == 1:
         return 'at ' + date.strftime("%I:%M %p") + ' tomorrow'
      elif diff.days > -7:
         return 'at ' + date.strftime("%I:%M %p %A")
      else:
         return 'on ' + date.strftime("%m/%d/%Y")
   else: return "(unknown)"
                 
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
         'tvrageid': int(tr.findall(rage['Episode URL']).pop()),
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
      rules = defaultdict(lambda: '')
      rules.update(rdict)
      query = urlencode({ 'searchaction': 'Search',
                'group': rules['group'],
                'category': 8,
                'u_completions': 9,
                'u_post_larger_than': rules['min-megs'],
                'u_post_smaller_than': rules['max-megs'],
                'q_url': ' or '.join([str(e.tvrageid) for e in sepis]),
                'sort': 'ps_edit_date',
                'order': 'asc',
                'u_post_results_amt': 500,
                'feed': 'csv' })
         
      search = urlopen("https://v3.newzbin.com/search/?%s" % query)
      results = dict((int(tr.findall(r[4])[0]), int(r[1]))
                 for r in csv.reader(search))
      search.close()
      
      for episode in sepis:
         if episode.newzbinid and episode.tvrageid not in results:
            episode.wasfake()
            
         if episode.tvrageid in results:
            episode.newzbinid = results[episode.tvrageid]

def save(tobesaved):
   with open(dbpath, 'w') as savefile:
      yaml.dump(tobesaved, savefile, indent=4, default_flow_style=False)

def load():
   with open(dbpath, 'r') as savefile:
      return yaml.load(savefile)


if os.path.exists(dbpath):
   showset = load()
   showset.refresh(config['sets'])
else:
   showset = Collection(config['sets'])

if options.run:
   for ruleset in config['sets']:
      inset = [e for e in showset.episodes() if e.show in ruleset['shows']]

      search_newzbin(inset, ruleset['rules'])
      showset.enqueue()

if options.status:
   showset.report()

save(showset)