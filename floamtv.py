#!/usr/bin/env python

# floamtv.py (Copyright 2008 Aaron Gyes)
# distributed under the GPLv3. See LICENSE

from __future__ import with_statement
import sys, re, os.path, csv, yaml, time, sched, signal
from urllib2 import urlopen
from urllib import urlencode
from optparse import OptionParser
from xmlrpclib import ServerProxy
from datetime import datetime as dt, timedelta
from collections import defaultdict

dbpath = os.path.expanduser('~/.floamtvdb2')
configpath = os.path.expanduser('~/.floamtvconfig2')
tr = re.compile(r"tvrage\.com/.*/([\d]{4,8})")
scheduler = sched.scheduler(time.time, time.sleep)

parser = OptionParser()
parser.add_option('-r', '--run', action='store_true', dest='run',
                  help='search newzbin and enqueue episodes that are ready.')
parser.add_option('-u', '--update', action='store_true', dest='updatedb',
                  help='update show information from TVRage.')
parser.add_option('--unwant', dest='unwant',
                  help='Set an episode to not download when available.')
parser.add_option('-s', '--status', dest='status', action='store_true',
                  help='Print information and status stuff.')
parser.add_option('--url', dest='url', action='store_true')
options, args = parser.parse_args()


if os.path.exists(configpath):
   with open(configpath, 'r') as configuration:
      config = yaml.load(configuration)
else:
   print 'You need to set up a config file first. See the docs.'
   sys.exit()
   

class Collection(yaml.YAMLObject):
   yaml_tag = '!Collection'
   
   def __init__(self, sets):
      self.shows = []
      self.refresh(sets)
   
   def refresh(self, sets):
      print 'Getting new data from TVRage.'
      for show in self.shows:
         show.update()
      
      shows = set()
      for aset in sets:
         shows.update(set(aset['shows']))
      
      alreadyhave = [t.title for t in self.shows]
      self.shows += [Show(s) for s in shows if s not in alreadyhave]
      
      self._save()
      scheduler.enter(60*60*2, 1, self.refresh, (sets,))
   
   def print_status(self, type='all'):
      print "\n Episodes we want\n"\
            " ================\n"
      for ep in self._episodes():
         if ep.wanted:
            print "  %s\n   %s\n" % (ep, relative_datetime(ep.airs))
   
   def look_on_newzbin(self, iffy=False):
      print 'Looking for new shows on newzbin.'
      for ruleset in config['sets']:
         inset = [e for e in self._episodes() if e.show in ruleset['shows']]
         search_newzbin(inset, ruleset['rules'])
      
      for ep in self._episodes():
         if ep.wanted and ep.newzbinid:
            if ep.airs and (ep.airs-dt.now()) > timedelta(hours=4) and not iffy:
               ep.was_fake(sure=False)
            else:
               ep.enqueue(allowiffy=iffy)
               
      self._save()
      scheduler.enter(60*8, 1, self.look_on_newzbin, (False,))
   
   def _save(self):
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
   
   def enqueue(self, allowiffy=False):
      if self.newzbinid and self.wanted != 'later' or allowiffy:
         hella = ServerProxy("http://hellanzb:%s@%s:8760"
                         % (config['hellanzb-pass'], config['hellanzb-host']))
         hella.enqueuenewzbin(self.newzbinid)
         
         checklog = lambda: hella.status()['log_entries'][-1].get('INFO')
         while checklog().startswith('Downloading'):
            time.sleep(0.5)
         else:
            print "Failed to enqueue %s" % self
            return
         log = checklog()

         print "Enqueued %s" % self
         self.wanted = False
   
   def was_fake(self, sure=True):
      if sure:
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
                'u_post_states': 3,
                'u_post_larger_than': rules['min-megs'],
                'u_post_smaller_than': rules['max-megs'],
                'q_url': ' or '.join([str(e.tvrageid) for e in sepis]),
                'sort': 'ps_edit_date',
                'order': 'desc',
                'u_post_results_amt': 500,
                'feed': 'csv' })
      
      search = urlopen("https://v3.newzbin.com/search/?%s" % query)
      
      if options.url:
         print search.geturl()
      
      results = dict((int(tr.findall(r[4])[0]), int(r[1]))
                 for r in csv.reader(search))
      search.close()
      
      for episode in sepis:
         if episode.newzbinid and episode.tvrageid not in results:
            if (episode.airs - dt.now()).days > -10:
               episode.was_fake()
         
         if episode.tvrageid in results:
            episode.newzbinid = results[episode.tvrageid]

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

def main():
   if os.path.exists(dbpath):
      showset = load()
   else:
      showset = Collection(config['sets'])

   if options.unwant:
      print "No longer want '%s'" % showset[options.unwant]

   elif options.status:
      showset.print_status()
   
   else:
      showset.look_on_newzbin(True)
      showset.refresh(config['sets'])
      scheduler.run()

if __name__ == '__main__':
   main()