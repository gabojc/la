#!/usr/bin/env python3
"""
Upload this file to a server, then run it on your Last.fm webapp server:
wget -O patch.py https://your-server.com/add_play_history.py && sudo python3 patch.py
"""
import re
from pathlib import Path

WEBAPP_PATH = Path("/opt/lastfm/webapp.py")

def patch():
    if not WEBAPP_PATH.exists():
        print(f"❌ Error: Could not find {WEBAPP_PATH}")
        return

    raw = WEBAPP_PATH.read_text(encoding="utf-8")

    # 1. Add 'Play History' to the sidebar navigation
    old_nav = """<div class="group">By month</div>"""
    new_nav = """<div class="group">History</div>
<ul>
<li><a href="#playhistory">Play History</a></li>
</ul>
<div class="group">By month</div>"""
    
    # 2. Add the HTML container for the new section
    old_html = """<div id="s_firstlast" hidden>"""
    new_html = """<div id="s_playhistory" hidden>
<h2>Play History</h2>
<p>Every scrobble with its play number, time since last play, time since previous play, and first-ever play.</p>
<div id="t_playhistory"></div>
</div>

<div id="s_firstlast" hidden>"""
    
    # 3. Add the JavaScript logic to fetch and render the data
    old_js = """    } else if (current === 'firstlast') {"""
    new_js = """    } else if (current === 'playhistory') {
      const lim = +$('top').value || 50;
      const q = $('q').value.trim().toLowerCase();
      
      fetch('data.json')
        .then(r => r.json())
        .then(data => {
          if (!data.play_history) {
            $('t_playhistory').innerHTML = '<p>No play history data available.</p>';
            return;
          }
          
          const rows = data.play_history
            .filter(x => !q || (x.artist + ' ' + x.track).toLowerCase().includes(q))
            .slice(0, lim)
            .map((x, i) => {
              const fmtRel = (ts) => {
                if (!ts) return '-';
                const diff = Date.now() / 1000 - ts;
                const days = Math.floor(diff / 86400);
                const hours = Math.floor((diff % 86400) / 3600);
                if (days > 0) return `${days}d ${hours}h ago`;
                if (hours > 0) return `${hours}h ago`;
                return 'Just now';
              };
              
              const fmtGap = (curr, prev) => {
                if (!prev) return '-';
                const diff = curr - prev;
                const days = Math.floor(diff / 86400);
                const hours = Math.floor((diff % 86400) / 3600);
                if (days > 0) return `${days}d ${hours}h`;
                if (hours > 0) return `${hours}h`;
                return 'Same session';
              };
              
              const playNum = x.play_num;
              const suffix = playNum % 10 === 1 && playNum % 100 !== 11 ? 'st' : 
                             playNum % 10 === 2 && playNum % 100 !== 12 ? 'nd' : 
                             playNum % 10 === 3 && playNum % 100 !== 13 ? 'rd' : 'th';
                             
              return {
                idx: i + 1,
                artist: x.artist,
                track: x.track,
                play_num: playNum,
                play_str: `${playNum}${suffix} play`,
                last_played: fmtRel(x.ts),
                prev_gap: fmtGap(x.ts, x.prev_ts),
                first_played: fmtRel(x.first_ts)
              };
            });
            
          const cols = [
            { h: '#', v: x => x.idx },
            { h: 'Track', v: x => x.artist + ' - ' + x.track },
            { h: 'Play #', v: x => x.play_num, dir0: -1 },
            { h: 'Time ago', v: x => x.last_played },
            { h: 'Gap before', v: x => x.prev_gap },
            { h: 'First play', v: x => x.first_played }
          ];
          
          renderTable($('t_playhistory'), 'playhistory', cols, rows);
        });
    } else if (current === 'firstlast') {"""
    
    # 4. Add the backend logic to lastfm_sync.py to generate this data
    # We will inject this right before it writes data.json
    sync_path = Path("/opt/lastfm/lastfm_sync.py")
    sync_raw = sync_path.read_text(encoding="utf-8")
    
    # Find the part where data.json is built. Usually looks like: data = { "s": ... }
    # We will inject a SQL query that builds the play_history array.
    # We assume the database has a 'scrobbles' table with 'utc', 'artist', 'track'.
    
    history_logic = """
    # --- INJECTED PLAY HISTORY ---
    play_history = []
    if 'conn' in locals() or 'conn' in globals():
        c = conn.cursor()
        # Get all scrobbles ordered by time
        c.execute("SELECT utc, artist, track FROM scrobbles ORDER BY utc ASC")
        all_s = c.fetchall()
        
        track_counts = {}
        track_first = {}
        track_prev = {}
        
        for utc, artist, track in all_s:
            key = (artist, track)
            track_counts[key] = track_counts.get(key, 0) + 1
            
            if key not in track_first:
                track_first[key] = utc
                
            play_history.append({
                "ts": utc,
                "artist": artist,
                "track": track,
                "play_num": track_counts[key],
                "first_ts": track_first[key],
                "prev_ts": track_prev.get(key, 0)
            })
            track_prev[key] = utc
            
    # Only keep the last 1000 scrobbles to keep data.json small
    data["play_history"] = play_history[-1000:]
    data["play_history"].reverse() # newest first
    # ----------------------------
    """
    
    if 'def build_data' in sync_raw:
        # Insert right before the return statement in build_data
        sync_raw = sync_raw.replace("return data", history_logic + "\n    return data", 1)
    else:
        # Fallback: insert before writing data.json
        sync_raw = sync_raw.replace('data["total"]', history_logic + '\n    data["total"]', 1)

    # Apply patches to webapp.py
    raw = raw.replace(old_nav, new_nav)
    raw = raw.replace(old_html, new_html)
    raw = raw.replace(old_js, new_js)
    raw = raw.replace("'unplayed', 'span', 'streaks'", "'unplayed', 'span', 'streaks', 'playhistory'")

    WEBAPP_PATH.write_text(raw, encoding="utf-8")
    sync_path.write_text(sync_raw, encoding="utf-8")
    
    print("✅ Patched webapp.py and lastfm_sync.py with Play History!")

if __name__ == "__main__":
    patch()
