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
import shutil, sys, re, os.path, csv, yaml
from urllib2 import urlopen
from urllib import urlencode
from optparse import OptionParser
from operator import itemgetter
from xmlrpclib import ServerProxy
from datetime import datetime as dt
from fuzzydict import FuzzyDict as Fuzzy

dbpath = os.path.expanduser('~/.floamtvdb2')
configpath = os.path.expanduser('~/.floamtvconfig2')

if os.path.exists(configpath):
   with open(configpath, 'r') as configuration:
      config = yaml.load(configuration)
else:
   print "You need to set up a config file first. See the docs."
   sys.exit()

tr = re.compile(r"tvrage\.com/.*/([\d]{6,8})")

parser = OptionParser()
parser.add_option('-r', '--run', action='store_true', dest='run',
                  help='search newzbin and enqueue episodes that are ready.')
parser.add_option('-u', '--update', action='store_true', dest='updatedb',
                  help='update show information from TVRage.')
parser.add_option('-p', '--pretend', action='store_true', dest='pretend',
                  help="don't actually do anything -- pretend to.")
parser.add_option('-s', '--show', dest='show',
                  help="show \"waitqueue\" or \"gotten\"")
parser.add_option('-a', '--add', action='append', dest='add', metavar='ID',
                  help='manually add episode to the waitqueue (by TVRage ID)',
                  type="int")                  
parser.add_option('-d', '--delete', action='append', dest='delete',
                  help='manually remove episode from waitqueue', metavar='ID')
parser.add_option('--ungotten', action='append', dest='ungotten', 
                  help='manually delete episode from the gotten list'
                  'ID.', metavar='ID')
options, args = parser.parse_args()


class Show(yaml.YAMLObject):
   yaml_tag = '!Show'
   def __init__(self, show):
      info = get_show_info(show)
      self.title = info["title"]
      self.episodes = dict()
      self.update(info)
      
   def add(self, episode):
      if episode and episode not in self.episodes:
         print "Adding %s" % episode
         self.episodes[episode] = Episode(self.title, episode)
   
   def update(self, rageinfo=None):
      if not rageinfo:
         rageinfo = get_show_info(self.title)
      self.recent = [rageinfo['latest'], rageinfo['next']]
      
      for ep in self.recent:
         self.add(ep)
      
      self.episodes = dict((n,e) for (n,e) in self.episodes.items()
                      if n in self.recent)
   
   def __repr__(self):
      return "<Show %s with episodes %s>" \
         % (self.title, ' and '.join(map(str, self.episodes)))
   

class Episode(yaml.YAMLObject):
   yaml_tag = '!Episode'
   def __init__(self, show, number):
      info = get_show_info(show, number)
      self.show = show
      self.number = info.get("epnum")
      self.title = info.get("eptitle")
      self.tvrageid = info.get("tvrageid")
      self.newzbinid = None
      self.wanted = True
      try:
         self.airs = dt.strptime(info.get("epairs"), "%d/%b/%Y; %A, %I:%M %p")
      except ValueError:
         self.airs = info.get("epairs")
   
   def __repr__(self):
      return "<Episode %s - %s - %s (%s)>" \
         % (self.show, self.number, self.title, self.tvrageid)

   
def get_show_info(show_name, episode=''):
   showdict = {}
   showinfo = urlopen("http://tvrage.com/quickinfo.php?%s"
                         % urlencode({ 'show': show_name, 'ep': episode }))
   result = showinfo.read()
   showinfo.close()
   
   if result.startswith('No Show Results'):
      raise Exception, "Show %s does not exist at tvrage." % show_name
   
   for line in result.splitlines():
      part = line.split('@')
      showdict[part[0]] = part[1].split('^') if '^' in part[1] else part[1]
   
   tvrageid = showdict.get("Episode URL")
   next = showdict.get("Next Episode")
   latest = showdict.get("Latest Episode")
   
   cleandict = {
      "title": showdict["Show Name"],
      "next": next[0] if next else None,
      "latest": latest[0] if latest else None,
   }
   
   if episode and tvrageid:
      more = {
         "tvrageid": int(tr.findall(tvrageid).pop()),
         "epnum": showdict["Episode Info"][0],
         "eptitle": showdict["Episode Info"][1],
         "epairs": showdict['Episode Info'][2] + ';' + showdict['Airtime']
      }
      cleandict.update(more)
   
   return cleandict

def enqueue(newzbinid):
   if options.pretend:
      print "Pretending to enqueue %s." % newzbinid
      return True
   else:
      hellanzb = ServerProxy("http://hellanzb:%s@localhost:8760"
                                                        % config['hellapass'])
      log = hellanzb.enqueuenewzbin(newzbinid)['log_entries'][-1]['INFO']
      if newzbinid in log:
         print "Enqueued %r" % newzbinid
         return True

def search_newzbin(tvids):
   query = { 'searchaction': 'Search',
             'group': config['rules']['group'],
             'category': 8,
             'u_completions': 9,
             'u_post_larger_than': config['rules']['min-megs'],
             'u_post_smaller_than': config['rules']['max-megs'],
             'q_url': ' or '.join(map(str, tvids.keys())),
             'sort': 'ps_edit_date',
             'order': 'asc',
             'u_post_results_amt': 500,
             'feed': 'csv' }
   search = urlopen("https://v3.newzbin.com/search/?%s" % urlencode(query))
   results = [(tr.findall(r[4]), r[1]) for r in csv.reader(search)]
   
   return dict((r[0], n) for (r, n) in results if r)


def save(tobesaved):
   with open(dbpath, 'w') as savefile:
      yaml.dump(shows, savefile, indent=4, default_flow_style=False)

def load():
   with open(dbpath, 'r') as savefile:
      return yaml.load(savefile)
   
if os.path.exists(dbpath):
   shows = load()
else:
   shows = {}

shows.update(dict((s,Show(s)) for s in config['shows'] if s not in shows))

save(shows)