floamtv.py
==========

**NOTE: this project was abandoned by me in 2010. I decided to convert my old bzr repo to a git one and put it out there.
It is certainly broken. It needs some love. The old README follows.**

`floamtv.py` is a robust Python application that automates downloading recurring
television shows that are tracked by [TVRage] [tvrage] and uploaded to Usenet.
It requires a membership with [NewzBin] [newzbin], and works with [hellanzb]
[hellanzb]. It is licensed under the GPLv3.

It works with [hellanzb] [hellanzb], a smart Unix NNTP client that runs in the
background and smartly fetches and processes usenet posts, fully extracting and
processing things smartly. The end result is a directory that magically has the
latest episodes of the shows you watch. It's like Tivo, but a lot more of a pain
to set up and of questionable legality depending on where you live. I only
recommend subscribing television with licenses that allow free redistribution if
you live in the United States.

Requirements
------------

* [hellanzb] [hellanzb]
* A [NewzBin] [newzbin] account
* Python 2.5
* Python modules:
    - [Twisted] [twisted] (you probably have this)
    - [PyYAML] [pyyaml]
    - [pytz] [pytz]
    
ChangeLog
---------

###Fixed in 77:
* Saving is now near-atomic, things going wrong at inopportune times should no
  longer corrupt `~/.floamtvdb2`.

###Fixed in 76:
* Less aggressively prune old episodes, should result in duplicates not happening
  when TVRage does something funny.
* Fix ID matching, was causing incorrect episodes to be downloaded.

###Fixed in 74:
* A bug caused filtering by group names not to work, with the effect of allowing
  reports from all groups. This has been fixed.

###Fixed in 73:
* A bug that caused an `AttributeError` exception on systems with old
  versions of Twisted has been resolved.

###New in 72:
* Daemonization support with `-D` switch.
* `--shutdown` command.
* Handle shows that air every day better.
* Logs into newzbin using your credentials. Look at the example configuration.
* Handle cases where the TVRage ID of a show changes.
* Logging is almost ready but not exposed yet.

###New in 62:
* Fixed two bugs relating to empty results from Newzbin and shows matching
  incorrectly. You might not have noticed but you might have had some shows that
  would not enqueue because we weren't finding them on newzbin even if they
  were posted.

###New in 60:
* **Complete rewrite**!
* New versioning scheme. Version numbers match the bazaar-ng revision number.
* You can now set different rules on different episodes!
* Timezone support!
* Much closer to rock-solid than old code. Now I actually know how I want it to
  work. Any bugs that existed before will likely be gone (replaced with new
  ones!)
* Way more solid against spam:
    * If an episode wasn't supposed to be airing this early, we wait a while to
      see if it ends up getting deleted before we take the plunge and download
      it.
    * The external handler stuff doesn't exist anymore. Shouldn't be necessary,
      and the idea of my script moving files around or deleting them is scary.
      Be sure to get rid of that line in your `hellanzb.conf`. 
    
* No longer ran periodically by `cron` or anything. Is a single long-running
  application.
* Way faster, does multiple HTTP connections at the same time using Twisted --
  grabs TVRage data in 3-5 seconds.
* Database format and configuration file is completely different. You'll need
  to re-read the documentation and start off from scratch.
* Database and configuration is now [YAML] [pyyaml]. This should be a lot
  more human readable and editable, and you can stick comments in it and stuff.
* It's one python file now -- there is no setup.py. Copy it where you want it.
  (this also means: make sure you delete the stuff the old setup.py copied.
  Search your hard disk for "floamtv" if all else fails.)
* Lots of other stuff.

###New in 0.23:
* Automatically delete spam posts when ran as a hellanzb external handler. See
  _"Spam Detection"_ below.
* Sorry! Fix another problem that kept `floamtv.py` from properly requeueing
  fakes.

###New in 0.22:
* Fixed the fake-detector -- it stopped working at some point.
* Now using urllib2's urlopen(), it's a tiny bit quicker.

###New in 0.21:
* Now has a setup.py

###New in 0.2:
* If a TV show disappears off newzbin sometime after we downloaded it, we
  can assume it was deleted because it was fake or bad or something. So we
  will re-add it to the waitqueue.

###0.1:
* Initial release to public.


Instructions
------------

Make sure you have the prerequisites: Python 2.5, PyYAML, pytz, and Twisted.

If you used previous versions of floamtv. Delete all traces of it. Everything
that started it periodically, everything it installed into `site-packages`. The
rewritten version is a long-running background process.

You can use `easy_install` if you have it for PyYAML, twisted, and pytz. You
should already have twisted if you're using hellanzb!

    joe@computer $ easy_install PyYAML pytz twisted

If you are a hero that was using it prior to version 60, make sure you get rid
of all traces of the old version.

    joe@computer $ locate floamtv
    /usr/local/bin/floamtv.py
    /Library/Python/2.5/site-packages/floamtv-0.24-py2.5.egg

    joe@computer $ rm /usr/local/bin/floamtv.py
    joe@computer $ rm /Library/Python/2.5/site-packages/floamtv-0.24-py2.5.egg

Install it however you like.

    joe@computer $ tar zxvf floamtv-77.tar.gz
    floamtv-77/
    floamtv-77/floamtv.py
    floamtv-77/floamtvconfig2
    floamtv-77/LICENSE
    floamtv-77/README
    
    joe@computer $ cp floamtv-77/floamtv.py /usr/local/bin

Once installed, edit the example configuration file with your favorite text
editor. Fill up the `shows` list with desired shows. Change the hellanzb
password to whatever you have set. Shows must be tracked by [TVRage] [1], so
if the script says some show doesn't exist, confirm that they've got it, and
that you're calling the show the same name as them. The searches are pretty
fuzzy so you usually don't need to worry about getting the title right down to
the letter. Edit and move the configuration (you'll find an example in the
tarball) to `~/.floamtvconfig2`. For reference, here is what it looks like:
    
    # Host and password for hellanzb RPC.
    hellanzb-pass: changeme
    hellanzb-host: localhost
    
    # How often we look for new posts on newzbin and new show information on 
    # TVRage in minutes. You might set them higher, but I suggest not lowering
    # them. Both services have banned IPs before for requesting too often.
    
    newzbin-interval: 8
    tvrage-interval: 300
    
    # Your news server's retention in days
    retention: 110
    
    sets:
      - shows:
          - Some TV Show
          - Another TV Show
          - "This Show: Quoted because it had a colon"
          - Super Awesome Rodent Masters
        timezone: US/Eastern
        rules:
          min-megs: 100
          max-megs: 805
          groups:
            - alt.binaries.tv
            - alt.binaries.multimedia

      - shows:
          - A British Show
        timezone: Europe/London
        rules:
          min-megs: 900
          max-megs: 2500
          query: "Attr: VideoF~x264"
          
As you might notice, it's a YAML format. Very easy for a human to edit.

You can set up more than one set if you want to give some shows a different set
of rules than others. You also need to separate out shows that air in different
timezones into different sets to that you can give them the correct timezone.
The timezone is important because while we might know from TVRage that a show
airs at 10PM, 10PM might be 7PM local time or 10AM local time. We use the air
time to try to detect fakes, so if set this part up wrong, you'll have problems.
The timezone strings are in Olson database form, and you can find a
[list on wikipedia](http://en.wikipedia.org/wiki/List_of_zoneinfo_timezones).
Just about every single American show airs on the east coast first, so most of
time time you'll use `US/Eastern`.

You could duplicate one show twice if you for perhaps wanted to download
both a low-quality and a high-quality 720p version of it.

The `query` field can be used for anything. Most people will use it for
[Attribute Searching] [attributes].

Once everything is to your desire,

Here's an example of Joe Blow getting `floamtv.py` going for the first time:

    joeblow@computer $ pico ~/.floamtvconfig2
    
    joeblow@computer $ floamtv.py
    Getting new data from TVRage.
    New episode: Battlestar Galactica - 04x02 - Six of One, [hq6k]
    New episode: Battlestar Galactica - 04x01 - He That Believeth In Me, [hpb4]
    New episode: Heroes - 03x01 - Villains, [hz0y]
    New episode: Heroes - 02x11 - Powerless, [hcvf]
    Quitting early to give you a chance to catch up.
    
The first time you run `floamtv.py`, after it finished downloading show
information it will quit. This gives you a chance to get rid of TV shows we
already have. We identify shows with the four letter identifier inside the
brackets. If there are episodes that you either don't want or already have, you
can tell floamtv that we don't want to download them with `--unwant`.

    joeblow@computer $ floamtv.py --unwant hcvf
    Will not download Heroes - 02x11 - Powerless, [hcvf] when available.

A cute shortcut, if you just want to `--unwant` everything that's already aired
is to `--unwant aired`.

We can check the status using `--status` or `-s`. (`--verbose` or `-v` will
include unwanted shows):

    joeblow@computer $ floamtv.py -sv
        (+) Battlestar Galactica - 04x02 - Six of One, [hq6k]
            (airs 07:00 PM Friday)
        (+) Battlestar Galactica - 04x01 - He That Believeth In Me, [hpb4]
            (aired 07:00 PM yesterday)
        (+) Heroes - 03x01 - Villains, [hz0y]
            (airs on 09/01/2008)
        ( ) Heroes - 02x11 - Powerless, [hcvf]
            (aired on 12/03/2007)

        ( ) = unwanted, (+) = wanted

Make sure you have hellanzb configured to be able to download NZBs from your
newzbin account. Yes, newzbin accounts cost money. It costs a few pennies
per week, and floamtv will probably never support using anything else. Newzbin
posts reference the TVRage ID number, which keeps everything very robust
because we never need to worry about matching episode names and numbers and
getting wrong matches.

### Spam Detection

One big problem are fake posts. Spammers on usenet will often post things that
look like real releases, but turn out to be winzix garbage and similar. It's a
tough problem because if it's been posted to newzbin, it's really tough to get
around. Here's what `floamtv.py` does to combat this:

* If a TV show we downloaded earlier has now been deleted from NewzBin, we
  assume it was fake and start trying to get it again.

* If a show pops up on Newzbin before the time it was supposed to air,
  we put it on "probation" -- we will only enqueue it with hellanzb if
  it is still on newzbin in two hours or when the shows was supposed to air,
  whichever comes first. Two hours is a while to wait, but if the show is a
  few days early anyways most people won't mind waiting. If it's somehow
  3 minutes early or there is a time discrepancy with TVRage making it appear
  an hour early, we only wait 3 minutes or an hour.

### Problems?

There are likely bugs in both this documentation and `floamtv`. I will be very
grateful if you clue me in on any you come across.

[tvrage]: http://tvrage.com
[twisted]: http://twistedmatrix.com
[newzbin]: http://v3.newzbin.com
[hellanzb]: http://hellanzb.com
[pytz]: http://pytz.sourceforge.net/
[pyyaml]: http://pyyaml.org/wiki/PyYAML
[attributes]: http://docs.newzbin.com/index.php/Newzbin:V3_Search#v3_Attribute_Searches
