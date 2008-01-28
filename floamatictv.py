#!/usr/bin/env python

# Floamatic TV Downloader (by Aaron Gyes)

# Fill up the shows database with public domain TV shows that TVRage knows
# about. Give it some rules, and it'll return newzbin report IDs of what you
# want. Use cron to update the database (-u) once or twice per day and search
# newzbin with it as often as you like. Shows are considered downloaded once
# it has told you about them. Use --pretend for a dry-run. --help for more
# information.

from __future__ import with_statement
import sys, re, pickle, os.path, csv, simplejson as json
from urllib import urlopen, urlencode
from optparse import OptionParser
from operator import itemgetter
from time import sleep
from xmlrpclib import ServerProxy

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

parser = OptionParser(version='Floamatic TV Downloader 0.2')
parser.add_option('-r', '--run', action='store_true', dest='run',
                  help='search newzbin and enqueue episodes that are ready.')
parser.add_option('-u', '--update', action='store_true', dest='updatedb',
                  help='update show information from TVRage.')
parser.add_option('-p', '--pretend', action='store_true', dest='pretend',
                  help="don't actually do anything -- pretend to.") 
parser.add_option('-w', '--show-waitqueue', action='store_true',dest='showwq',
                  help="show episodes we're waiting for")                  
parser.add_option('-b', '--show-blacklist', action='store_true',dest='showbl',
                  help="show episodes we're not going to queue")
parser.add_option('-a', '--add', action='append', dest='add', type='int', 
                  help='manually add episode to the waitqueue (by TVRage ID)',
                  metavar='ID')                  
parser.add_option('-d', '--delete', action='append', dest='delete',type='int',
                  help='manually remove episode from waitqueue', metavar='ID')
parser.add_option('--unblacklist', action='append', dest='unblacklist', 
                  help='manually delete episode from the blacklist'
                  'ID.', metavar='ID', type='int')
options, args = parser.parse_args()

def updatedb():
   print 'Creating show database, this can take a while.'
   options.updatedb = False # avoid recursion
   db, wq, donotwant = load_stuff() if os.path.exists(dbpath) else ({},{},[])
   db, newqueue = make_showdicts(shows, donotwant)
   wq.update(newqueue)
   save_stuff(db, wq, donotwant)
   return db, wq, donotwant

def get_show_info(show_name, episode=''):
   showinfo = urlopen("http://tvrage.com/quickinfo.php?%s"
                         % urlencode({ 'show': show_name, 'ep': episode }))
   result = showinfo.read()
   
   if result.startswith('No Show Results'):
      raise Exception, "Show %s does not exist at tvrage." % show_name
   
   showdict = {}
   for line in result.strip().splitlines():
      line = line.split('@')
      showdict[line[0]] = line[1].split('^') if '^' in line[1] else line[1]
      
   if episode:
      if showdict.has_key('Episode URL'):
         return int(re.findall(r"[\d]+", showdict['Episode URL'])[-1])
      else: return None
   
   for t in ['Latest Episode', 'Next Episode']:
      if showdict.has_key(t):
         showdict[t].append(get_show_info(show_name, showdict[t][0]))   
   return showdict

def make_showdicts(shows, dontwant):
   db, wq = {}, {}
   
   for show in shows:
      info = get_show_info(show)
      db[info['Show Name']] = info
      for t in ['Latest Episode', 'Next Episode']:
         if info.has_key(t) and info[t][3] not in dontwant and info[t][3]:
            wq[info[t][3]] = "%s %s: %s" % (info['Show Name'],
                                             info[t][0], info[t][1])
   return db, wq

def enqueue(newzbinid):
   if options.pretend:
      print "Pretending to enqueue %s." % newzbinid
      return True
   else:
      hellanzb = ServerProxy("http://hellanzb:%s@localhost:8760" % hellapass)
      log = hellanzb.enqueuenewzbin(newzbinid)['log_entries'][-1]['INFO']
      if str(newzbinid) in log:
         print "Enqueued %d" % newzbinid
         return True

def search_newzbin(tvids):
   query = { 'searchaction': 'Search',
             'group': rules['group'],
             'category': 8,
             'u_post_larger_than': rules['min-megs'],
             'u_post_smaller_than': rules['max-megs'],
             'q_url': ' or '.join(map(str, tvids.keys())),
             'sort': 'ps_edit_date',
             'order': 'asc',
             'u_post_results_amt': 500,
             'feed': 'csv' }
   tr = re.compile(r"tvrage\.com/.*/([\d]{6,8})")
   search = urlopen("https://v3.newzbin.com/search/?%s" % urlencode(query))
   results = [(tr.findall(r[4]), r[1]) for r in csv.reader(search)]
   return dict([(int(r[0]), int(n)) for (r, n) in results if r])

def save_stuff(db, waitqueue, bl):
   if not options.pretend:
      with open(dbpath, 'w') as dumpfile:
         json.dump([db, waitqueue, bl], dumpfile, indent=2)

def load_stuff():
   if not os.path.exists(dbpath) or options.updatedb:
      db, waitqueue, donotwant = updatedb()
      options.updatedb = True
   else:
      with open(dbpath, 'r') as dbf:
         db, waitqueue, donotwant = json.load(dbf)
      waitqueue = dict([(int(k), v) for (k, v) in waitqueue.items()])
   return db, waitqueue, sorted(donotwant)


db, waitqueue, donotwant = load_stuff()

if options.add:
   for tvid in options.add:
      if not waitqueue.has_key(tvid):
         waitqueue[tvid] = 'Added manually'
   save_stuff(db, waitqueue, donotwant)

if options.delete:
   for tvid in options.delete:
      try:
         del waitqueue[tvid]
         donotwant.append(tvid)
      except KeyError:
         print "ID %d not in waitqueue." % tvid
   save_stuff(db, waitqueue, donotwant)

if options.unblacklist:
   for tvid in options.unblacklist:
      try:
         donotwant.remove(tvid)
      except ValueError:
         print "ID %d not in blacklist." % tvid
   save_stuff(db, waitqueue, donotwant)

if options.run:
   nbids = search_newzbin(waitqueue)
   for rageid in nbids:
      enqueue(nbids[rageid])
      del waitqueue[rageid]
      donotwant.append(rageid)
      save_stuff(db, waitqueue, donotwant)

if options.showwq:
   print "Episodes we're waiting for:\n"
   print " TVRage ID\tShow"
   print " =========\t===="
   print "\n".join("%8r\t%s" % (i[0], waitqueue[i[0]]) for i in
                             sorted(waitqueue.iteritems(), key=itemgetter(1)))
if options.showbl:
   print "Episodes we will not queue:\n"
   print " TVRage ID"
   print " ========="
   print "\n".join("%8r" % tvid for tvid in donotwant)

if not any(options.__dict__.itervalues()) and __name__ == '__main__':
   parser.print_help()