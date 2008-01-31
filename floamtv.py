#!/usr/bin/env python

# floamtv.py (Copyright 2008 Aaron Gyes)
# distributed under the GPLv3. See LICENSE

# Fill up the shows database with public domain TV shows that TVRage knows
# about. Give it some rules, and it'll return newzbin report IDs of what you
# want. Use cron to update the database (-u) once or twice per day and search
# newzbin with it as often as you like. Shows are considered downloaded once
# it has told you about them. Use --pretend for a dry-run. --help for more
# information.

from __future__ import with_statement
import re, os.path, csv, simplejson as json
from urllib import urlopen, urlencode
from optparse import OptionParser
from operator import itemgetter
from time import sleep
from xmlrpclib import ServerProxy
from fuzzydict import FuzzyDict as Fuzzy

# Configuration

dbpath = os.path.expanduser('~/.fshowdb')
hellapass = 'changeme'

# TODO: Way more terms!
rules = {
   'min-megs': 100,
   'max-megs': 805,
   'group': ''
}

shows = ['Mythbusters', 'Stargate Atlantis', 'The Simpsons', 'Law & Order',
         'Dexter', 'LOST', 'Heroes', 'Doctor Who (2005)',
         'Battlestar Galactica', 'Dirty Jobs', 'Eureka',
         'The Daily Show', 'Prison Break', 'Law & Order: SVU',
         'Law and Order: Criminal Intent', 'Numb3rs', 'Family Guy',
         'South Park', 'COPS', 'The Office', 'House', 'The Colbert Report'
]

# Normal usage shouldn't require any edits past this line.

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

def updatedb():
   print 'Updating show database, this can take a while.'
   options.updatedb = False # avoid recursion
   
   wq, gotten = load_stuff() if os.path.exists(dbpath) else ( {}, {} )
   wq2, rejected = get_tvids(shows, gotten)
   wq.update(wq2)
   # Prune old episodes:
   [gotten.pop(ep) for ep in gotten.copy() if ep not in rejected]
   save_stuff(wq, gotten)
   return wq, gotten

def get_show_info(show_name, episode=''):
   showdict = {}
   showinfo = urlopen("http://tvrage.com/quickinfo.php?%s"
                         % urlencode({ 'show': show_name, 'ep': episode }))
   result = showinfo.read()

   
   if result.startswith('No Show Results'):
      raise Exception, "Show %s does not exist at tvrage." % show_name
   
   for line in result.splitlines():
      part = line.split('@')
      showdict[part[0]] = part[1].split('^') if '^' in part[1] else part[1]

   if episode:
      if showdict.has_key("Episode URL"):
         tvrageid = tr.findall(showdict['Episode URL'])
      else:
         tvrageid = None
         
      showdict["ID"] = tvrageid.pop() if tvrageid else None
   return showdict

def get_tvids(shows, gotten):
   waitqueue, rejected = {}, []
   for show in shows:
      info = get_show_info(show)

      for t in ['Latest Episode', 'Next Episode']:
         if info.has_key(t):
            episode = get_show_info(show, info[t][0])
            if episode["ID"]:
               if episode["ID"] not in gotten:
                  waitqueue[episode["ID"]] = "%s %s: %s" % (info['Show Name'],
                                                      info[t][0], info[t][1])
               else:
                  rejected.append(episode["ID"])
               
   return waitqueue, rejected
      
def enqueue(newzbinid):
   if options.pretend:
      print "Pretending to enqueue %s." % newzbinid
      return True
   else:
      hellanzb = ServerProxy("http://hellanzb:%s@localhost:8760" % hellapass)
      log = hellanzb.enqueuenewzbin(newzbinid)['log_entries'][-1]['INFO']
      if newzbinid in log:
         print "Enqueued %r" % newzbinid
         return True

def search_newzbin(tvids):
   query = { 'searchaction': 'Search',
             'group': rules['group'],
             'category': 8,
             'u_completions': 9,
             'u_post_larger_than': rules['min-megs'],
             'u_post_smaller_than': rules['max-megs'],
             'q_url': ' or '.join(map(str, tvids.keys())),
             'sort': 'ps_edit_date',
             'order': 'asc',
             'u_post_results_amt': 500,
             'feed': 'csv' }
   search = urlopen("https://v3.newzbin.com/search/?%s" % urlencode(query))
   results = [(tr.findall(r[4]), r[1]) for r in csv.reader(search)]
   
   return dict((r[0], n) for (r, n) in results if r)

def save_stuff(waitqueue, gotten):
   if not options.pretend:
      with open(dbpath, 'w') as dumpfile:
         json.dump([waitqueue, gotten], dumpfile, indent=2)

def load_stuff():
   if not os.path.exists(dbpath) or options.updatedb:
      waitqueue, gotten = updatedb()
      options.updatedb = True
   else:
      with open(dbpath, 'r') as dbf:
         waitqueue, gotten = json.load(dbf)
   return waitqueue, gotten

def swap(d):
   return dict((v, k) for (k, v) in d.iteritems())

waitqueue, gotten = load_stuff()

if options.add:
   for tvid in options.add:
      if not waitqueue.has_key(tvid):
         waitqueue[tvid] = '(manually)'
   save_stuff(waitqueue, gotten)

if options.delete:
   for given in options.delete:
      try:
         gotten[given] = waitqueue.pop(given)
      except KeyError:
         try:
            rfuzzy = Fuzzy(swap(waitqueue), cutoff=0.32)[given]
            gotten[rfuzzy] = waitqueue.pop(rfuzzy) + " (manually)"
         except KeyError:
            print "Couldn't find %r in waitqueue." % given
            
   save_stuff(waitqueue, gotten)

if options.run:
   nbids = search_newzbin(waitqueue)
   for rageid, nbid in nbids.iteritems():
      enqueue(nbid)
      gotten[rageid] = waitqueue.pop(rageid)
      save_stuff(waitqueue, gotten)
   oldids = search_newzbin(gotten) # (looking for fakes)
   for rageid, title in gotten.iteritems():
      if rageid not in oldids and not title.endswith("(manually)"):
         print "%s was deleted from newzbin sometime after we queued" % title
         print ' it. It was probably fake. Readding to the waitlist.'
         options.ungotten = [rageid]

if options.ungotten:
   for given in options.ungotten:
      try:
         del gotten[given]
      except KeyError:
         try:
            del gotten[Fuzzy(swap(gotten), cutoff=0.32)[given]]
         except KeyError:   
            print "Couldn't find %r in gottenlist." % given

   save_stuff(waitqueue, gotten)
         

sopts = Fuzzy({"waitqueue": waitqueue, "gotten": gotten}, cutoff=0.2)
if options.show in sopts: 
   print " TVRage ID\tShow"
   print " =========\t===="
   print "\n".join("%8s\t%s" % (k, v) for k, v in
                   sorted(sopts[options.show].iteritems(), key=itemgetter(1)))

if not any(options.__dict__.itervalues()) and __name__ == '__main__':
   parser.print_help()