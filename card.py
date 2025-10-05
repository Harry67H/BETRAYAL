# app.py
# Single-file Flask prototype for BETRAYAL (includes card image handling)
# Place your card files in: static/cards/
# - killcard.psd
# - statecard.psd
# - accusatecard.psd
# - judgecard.psd
# - shieldcard.psd
# - takecard.psd
# - exchangecard.psd
# - searchcard.psd
#
# Optionally also add PNG versions (recommended), e.g. statecard.png etc.
#
# Requirements: Flask, Werkzeug
# Run: python app.py

import os
import sqlite3
import random
import uuid
import time
from datetime import timedelta
from flask import (
    Flask, g, render_template_string, request, redirect, url_for, session,
    send_from_directory, jsonify, flash, abort, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ---------- CONFIG ----------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, 'static')
CARDS_DIR = os.path.join(STATIC_DIR, 'cards')
UPLOAD_FOLDER = os.path.join(STATIC_DIR, 'uploads')
DB_PATH = os.path.join(APP_DIR, 'betrayal.db')
ALLOWED_AVATAR = {'png', 'jpg', 'jpeg', 'gif'}
MATCH_SIZE = 5  # you + 4 others (>= 4 other players)
SECRET_KEY = os.environ.get('FLASK_SECRET', 'change_me_for_prod')

os.makedirs(CARDS_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=30)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ---------- Card names (filenames without extension) ----------
# These are the exact names you requested.
CARD_FILES = [
    "statecard",
    "searchcard",
    "shieldcard",
    "takecard",
    "exchangecard",
    "judgecard",
    "killcard",
    "accusatecard"
]

# Mapping to user-visible TYPE names
CARD_LABEL = {
    "statecard": "STATE",
    "searchcard": "SEARCH",
    "shieldcard": "SHIELD",
    "takecard": "TAKE",
    "exchangecard": "EXCHANGE",
    "judgecard": "JUDGE",
    "killcard": "KILL",
    "accusatecard": "ACCUSATION"
}

# Build standard deck: 5 of each of the 6 main cards + 1 kill + 1 accusate => 32
def make_new_deck():
    deck = []
    for c in ["statecard", "searchcard", "shieldcard", "takecard", "exchangecard", "judgecard"]:
        deck += [c] * 5
    deck += ["killcard", "accusatecard"]
    random.shuffle(deck)
    return deck

# ---------- DB helpers ----------
def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    cur = db.cursor()
    cur.execute('''
      CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        level INTEGER DEFAULT 1,
        avatar TEXT
      )
    ''')
    db.commit()

@app.teardown_appcontext
def close_conn(exc):
    db = getattr(g, '_db', None)
    if db is not None:
        db.close()

# ---------- Utilities ----------
WORD_A = ["Silent","Rusty","Gamma","Crimson","Lucky","Shadow","Frost","Nova","Brave","Sly"]
WORD_B = ["Fox","Brick","Orbit","Beacon","Vortex","Panda","Gadget","Ranger","Bolt","Drift"]
def generate_random_username():
    return random.choice(WORD_A) + random.choice(WORD_B) + str(random.randint(1,99))

def allowed_file(filename, allowed):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in allowed

def current_user():
    if 'user_id' not in session:
        return None
    db = get_db()
    cur = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    return cur.fetchone()

# ---------- In-memory matchmaking & games ----------
matchmaking_queue = []
queue_map_lock = False  # tiny lock flag for simplicity (not perfect but OK for prototype)
games = {}  # game_id -> game dict
user_to_game = {}  # user_id -> game_id mapping for quick lookup

def create_game_for_players(player_ids):
    """
    Create a game state: deck, hands, used_pile, killer (if assigned by initial deal),
    alive list, log, shields equipped.
    """
    deck = make_new_deck()
    hands = {}
    for pid in player_ids:
        hands[pid] = [deck.pop() for _ in range(3)]
    # after dealing, killer = whoever has killcard in hand (or None)
    killer = None
    for pid, hand in hands.items():
        if 'killcard' in hand:
            killer = pid
            break
    game = {
        'id': str(uuid.uuid4())[:8],
        'players': player_ids.copy(),
        'alive': player_ids.copy(),
        'hands': hands,
        'deck': deck,
        'used': [],
        'killer': killer,
        'shields_equipped': {},  # user_id -> True if shield equipped (kept until attempted kill)
        'log': [],
        'started_at': time.time(),
        'finished': False,
        'winner': None
    }
    games[game['id']] = game
    for pid in player_ids:
        user_to_game[pid] = game['id']
    return game

# ---------- Card image endpoint ----------
@app.route('/card/<card_name>')
def card_image(card_name):
    """
    Serve a PNG if present at static/cards/<card_name>.png.
    If not, but a PSD exists (static/cards/<card_name>.psd), return a generated SVG placeholder
    that clearly labels the card name (so the UI shows something even if you only have PSD files).
    """
    # sanitize: only allow known card names
    if card_name not in CARD_FILES:
        abort(404)
    png_path = os.path.join(CARDS_DIR, f"{card_name}.png")
    psd_path = os.path.join(CARDS_DIR, f"{card_name}.psd")
    if os.path.exists(png_path):
        return send_from_directory(CARDS_DIR, f"{card_name}.png")
    if os.path.exists(psd_path):
        # Return a small SVG placeholder that shows the card label and mentions PSD present.
        label = CARD_LABEL.get(card_name, card_name).upper()
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="380" height="560" viewBox="0 0 380 560">
  <rect x="2" y="2" width="376" height="556" rx="16" ry="16" fill="#111" stroke="#eee" stroke-width="4"/>
  <text x="50%" y="38%" fill="#fff" font-size="34" font-family="Arial" text-anchor="middle">{label}</text>
  <text x="50%" y="55%" fill="#ccc" font-size="14" font-family="Arial" text-anchor="middle">PSD file present (</text>
  <text x="50%" y="60%" fill="#ccc" font-size="12" font-family="Arial" text-anchor="middle">{card_name}.psd)</text>
  <text x="50%" y="86%" fill="#aaa" font-size="10" font-family="Arial" text-anchor="middle">Convert to PNG for best web display</text>
</svg>'''
        return Response(svg, mimetype='image/svg+xml')
    # Neither PNG nor PSD present — return a simple SVG 'missing' placeholder:
    svg2 = f'''<svg xmlns="http://www.w3.org/2000/svg" width="380" height="560" viewBox="0 0 380 560">
  <rect x="2" y="2" width="376" height="556" rx="16" ry="16" fill="#222" stroke="#ff6666" stroke-width="4"/>
  <text x="50%" y="50%" fill="#ff6666" font-size="18" font-family="Arial" text-anchor="middle">MISSING: {card_name}</text>
  <text x="50%" y="80%" fill="#bbb" font-size="10" font-family="Arial" text-anchor="middle">Place {card_name}.psd or {card_name}.png in static/cards/</text>
</svg>'''
    return Response(svg2, mimetype='image/svg+xml')

# ---------- Basic templates ----------
layout = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>BETRAYAL</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background:#0f0f12; color:#eee; }
    .play-btn { background: red; color: black; font-weight: 700; padding: .6rem 1rem; border-radius: .5rem; }
    .center-screen { display:flex; align-items:center; justify-content:center; min-height:60vh; flex-direction:column; gap:1rem; }
    .card-image { width:120px; margin: .2rem; border-radius:8px; border:2px solid rgba(255,255,255,0.06); box-shadow: 0 6px 18px rgba(0,0,0,0.6); cursor:pointer; transition: transform .12s ease; }
    .card-image:hover { transform: translateY(-6px) scale(1.02); }
    .hand { display:flex; align-items:center; justify-content:center; gap:8px; padding:12px; }
    .muted { color:#9aa; font-size:0.9rem }
    .game-log { max-height:180px; overflow:auto; background:#0b0b0d; padding:8px; border-radius:8px; }
  </style>
</head>
<body>
<div class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h3>BETRAYAL</h3>
    <div>
      {% if user %}
        <span>{{ user['username'] }} (lvl{{ user['level'] }})</span>
        <a href="{{ url_for('logout') }}" class="btn btn-sm btn-outline-light ms-2">Logout</a>
      {% else %}
        <a href="{{ url_for('login') }}" class="btn btn-sm btn-outline-light">Login</a>
        <a href="{{ url_for('signup') }}" class="btn btn-sm btn-outline-light ms-2">Signup</a>
      {% endif %}
    </div>
  </div>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-warning">{{ messages[0] }}</div>
    {% endif %}
  {% endwith %}
  {% block body %}{% endblock %}
</div>
</body>
</html>
"""

# ---------- Routes: index, auth ----------
@app.before_request
def ensure_db():
    init_db()

@app.route('/')
def index():
    user = current_user()
    return render_template_string(layout + """
      {% block body %}
      <div class="center-screen">
        <!-- BETRAYAL.psd displayed here if present (SVG fallback if not) -->
        <img src="{{ url_for('static', filename='BETRAYAL.psd') }}"
             onerror="this.onerror=null; this.src='{{ url_for('static', filename='BETRAYAL.png') if (('/static/BETRAYAL.png')|length) else '' }}';"
             alt="BETRAYAL" style="max-width:40%; border: 4px solid #fff;">
        <div>
          <a href="{{ url_for('play') }}" class="play-btn">Play</a>
        </div>
        <p class="muted">Prototype: please add card files into <code>static/cards/</code></p>
      </div>
      {% endblock %}
    """, user=user)

@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username') or generate_random_username()
        password = request.form.get('password')
        avatar = request.files.get('avatar')
        if not password or not username:
            flash("Username and password required.")
            return redirect(url_for('signup'))
        db = get_db()
        cur = db.cursor()
        try:
            avatar_filename = None
            if avatar and allowed_file(avatar.filename, ALLOWED_AVATAR):
                fn = secure_filename(avatar.filename)
                avatar_filename = f"{uuid.uuid4().hex}_{fn}"
                avatar.save(os.path.join(app.config['UPLOAD_FOLDER'], avatar_filename))
            ph = generate_password_hash(password)
            cur.execute('INSERT INTO users (username,password_hash,level,avatar) VALUES (?,?,1,?)',
                        (username, ph, avatar_filename))
            db.commit()
            uid = cur.lastrowid
            session.permanent = True
            session['user_id'] = uid
            return redirect(url_for('index'))
        except sqlite3.IntegrityError:
            flash("Username already exists. Try another or leave blank for random.")
            return redirect(url_for('signup'))
    user = current_user()
    return render_template_string(layout + """
      {% block body %}
      <div class="row justify-content-center">
        <div class="col-md-6">
          <div class="card bg-secondary text-light p-3">
            <h4>Sign up</h4>
            <form method="post" enctype="multipart/form-data">
              <div class="mb-2">
                <label>Username (optional — leave empty for random)</label>
                <input class="form-control" name="username" placeholder="or leave blank for random">
              </div>
              <div class="mb-2">
                <label>Password</label>
                <input class="form-control" name="password" type="password" required>
              </div>
              <div class="mb-2">
                <label>Profile picture (optional)</label>
                <input class="form-control" name="avatar" type="file" accept="image/*">
              </div>
              <button class="btn btn-light">Create account</button>
            </form>
          </div>
        </div>
      </div>
      {% endblock %}
    """, user=user)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        db = get_db()
        cur = db.execute('SELECT * FROM users WHERE username = ?', (username,))
        row = cur.fetchone()
        if not row or not check_password_hash(row['password_hash'], password):
            flash("Invalid credentials")
            return redirect(url_for('login'))
        session.permanent = True
        session['user_id'] = row['id']
        return redirect(url_for('index'))
    user = current_user()
    return render_template_string(layout + """
      {% block body %}
      <div class="row justify-content-center">
        <div class="col-md-6">
          <div class="card bg-secondary text-light p-3">
            <h4>Login</h4>
            <form method="post">
              <div class="mb-2"><label>Username</label><input class="form-control" name="username" required></div>
              <div class="mb-2"><label>Password</label><input class="form-control" name="password" type="password" required></div>
              <button class="btn btn-light">Login</button>
            </form>
          </div>
        </div>
      </div>
      {% endblock %}
    """, user=user)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# ---------- Play menu ----------
@app.route('/play')
def play():
    user = current_user()
    if not user:
        flash("Please login or sign up to play.")
        return redirect(url_for('login'))
    return render_template_string(layout + """
      {% block body %}
      <div class="center-screen">
        <h4>Select Mode</h4>
        <div class="d-flex gap-3">
          <a class="btn btn-outline-light" href="{{ url_for('bots_mode') }}">BOTS</a>
          <a class="btn btn-outline-light" href="{{ url_for('online_mode') }}">ONLINE</a>
        </div>
      </div>
      {% endblock %}
    """, user=user)

# ---------- Bots mode (shows player hand with card images + quick sim) ----------
@app.route('/bots', methods=['GET','POST'])
def bots_mode():
    user = current_user()
    if not user:
        return redirect(url_for('login'))

    # Build deck and deal to 5 players (YOU + 4 bots)
    players = ['YOU'] + [f"BOT{i}" for i in range(1,5)]
    deck = make_new_deck()
    hands = {p: [deck.pop() for _ in range(3)] for p in players}

    # find killer (whoever has killcard)
    killer = None
    for p in players:
        if 'killcard' in hands[p]:
            killer = p
            break
    if not killer:
        # if for some reason not dealt, place killcard into remaining deck and give to random bot
        deck.append('killcard')
        random.shuffle(deck)
        hands[random.choice(players)].append(deck.pop())

    # quick deterministic-leaning bot simulation (not full rules, just demo)
    alive = players.copy()
    used = []
    log = []
    round_no = 0
    while True:
        round_no += 1
        # Each alive player acts (very simple)
        for p in alive.copy():
            # killer tries to kill somebody occasionally
            if p == killer:
                if len(alive) <= 1:
                    break
                if random.random() < 0.6:
                    targets = [x for x in alive if x != p]
                    tgt = random.choice(targets)
                    # check shield equip (we treat 'shieldcard' in hand as equipped for bots demo)
                    if 'shieldcard' in hands[tgt]:
                        hands[tgt].remove('shieldcard')
                        used.append('shieldcard')
                        log.append(f"{tgt}'s SHIELD broke.")
                    else:
                        alive.remove(tgt)
                        used += hands[tgt]
                        hands[tgt] = []
                        log.append(f"{tgt} was killed by the killer.")
                else:
                    log.append(f"{p} (killer) skipped killing.")
            else:
                # non-killer bot: 10% chance to play accusate if they have it
                if 'accusatecard' in hands[p] and random.random() < 0.15:
                    accused = random.choice(alive)
                    if accused == killer:
                        log.append(f"{p} used ACCUSATION and correctly accused {accused}.")
                        # faithfuls win
                        alive = [x for x in alive if x != killer]
                        used += hands[killer]
                        hands[killer] = []
                        killer = None
                        break
                    else:
                        log.append(f"{p} used ACCUSATION and was wrong → {p} eliminated.")
                        alive.remove(p)
                        used += hands[p]
                        hands[p] = []
            # simple safety break
            if len(alive) <= 1:
                break
        # termination conditions
        if killer is None or winner_by_killer(alive, killer):
            break
        if round_no > 8:
            break

    human_won = ('YOU' in alive) and (killer not in alive)
    if human_won:
        db = get_db()
        db.execute('UPDATE users SET level = level + 1 WHERE id = ?', (user['id'],))
        db.commit()

    return render_template_string(layout + """
      {% block body %}
      <div>
        <h4>Bots match (demo)</h4>
        <div class="row">
          <div class="col-md-8">
            <h5>Your hand</h5>
            <div class="hand">
              {% for c in your_hand %}
                <div>
                  <img src="{{ url_for('card_image', card_name=c) }}" class="card-image" alt="{{ c }}">
                  <div class="muted text-center">{{ card_labels[c] }}</div>
                </div>
              {% endfor %}
            </div>
            <p class="muted">(Cards shown as images from static/cards/&lt;name&gt;.png or placeholder SVG if only PSD exists.)</p>
            <h5>Game log</h5>
            <div class="game-log">
              {% for row in log %}
                <div>{{ row }}</div>
              {% endfor %}
            </div>
          </div>
          <div class="col-md-4">
            <h5>Result</h5>
            <p>Killer: <strong>{{ killer }}</strong></p>
            <p>Alive at end: {{ alive }}</p>
            <p>{% if human_won %}You won — level increased{% else %}You lost — try again{% endif %}</p>
            <a href="{{ url_for('play') }}" class="btn btn-light">Back</a>
          </div>
        </div>
      </div>
      {% endblock %}
    """,
    user=user,
    your_hand=hands['YOU'],
    card_labels=CARD_LABEL,
    log=log,
    killer=killer,
    alive=alive,
    human_won=human_won)

def winner_by_killer(alive, killer):
    """killer wins if killer is alive and everyone else is dead"""
    if killer is None:
        return False
    if killer in alive and len([p for p in alive if p != killer]) == 0:
        return True
    return False

# ---------- Online matchmaking ----------
@app.route('/online')
def online_mode():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    return render_template_string(layout + """
      {% block body %}
      <div class="center-screen">
        <h4>Online matchmaking</h4>
        <p>Click "Search" to join the queue. Match starts when {{ MATCH_SIZE }} players are waiting (you included).</p>
        <button id="searchBtn" class="btn btn-light">Search</button>
        <button id="stopBtn" class="btn btn-outline-light">Stop</button>
        <div class="mt-3">
          <p>Queue size: <span id="qsize">0</span></p>
        </div>
      </div>
      <script>
        const searchBtn = document.getElementById('searchBtn');
        const stopBtn = document.getElementById('stopBtn');
        let watching = false;
        searchBtn.onclick = async () => {
          await fetch('{{ url_for("join_queue") }}', {method:'POST'});
          watching = true; pollQueue();
        }
        stopBtn.onclick = async () => {
          await fetch('{{ url_for("leave_queue") }}', {method:'POST'});
          watching = false;
        }
        async function pollQueue(){
          if(!watching) return;
          const res = await fetch('{{ url_for("queue_status") }}');
          const data = await res.json();
          document.getElementById('qsize').innerText = data.size;
          if(data.start_game){
            window.location = '/game/' + data.game_id;
            return;
          }
          setTimeout(pollQueue, 1400);
        }
      </script>
      {% endblock %}
    """, user=user, MATCH_SIZE=MATCH_SIZE)

@app.route('/match/join', methods=['POST'])
def join_queue():
    user = current_user()
    if not user:
        return jsonify({'error':'login required'}), 401
    uid = user['id']
    if uid not in matchmaking_queue:
        matchmaking_queue.append(uid)
    # create match if enough
    if len(matchmaking_queue) >= MATCH_SIZE:
        players = [matchmaking_queue.pop(0) for _ in range(MATCH_SIZE)]
        g = create_game_for_players(players)
        return jsonify({'started': True, 'game_id': g['id']})
    return jsonify({'started': False})

@app.route('/match/leave', methods=['POST'])
def leave_queue():
    user = current_user()
    if not user:
        return jsonify({'error':'login required'}), 401
    uid = user['id']
    if uid in matchmaking_queue:
        matchmaking_queue.remove(uid)
    return jsonify({'left': True})

@app.route('/match/status')
def queue_status():
    user = current_user()
    if not user:
        return jsonify({'error':'login required'}), 401
    uid = user['id']
    gid = user_to_game.get(uid)
    size = len(matchmaking_queue)
    if gid:
        return jsonify({'size': size, 'start_game': True, 'game_id': gid})
    return jsonify({'size': size, 'start_game': False})

# ---------- Game room ----------
@app.route('/game/<game_id>')
def game_room(game_id):
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    g = games.get(game_id)
    if not g:
        flash("Game not found or ended.")
        return redirect(url_for('play'))
    uid = user['id']
    if uid not in g['players']:
        flash("You're not in this game.")
        return redirect(url_for('play'))
    # Show only your hand (not other players' hands). Show simple controls to play a card (demo).
    your_hand = g['hands'].get(uid, [])
    return render_template_string(layout + """
      {% block body %}
      <div>
        <h4>Game room — demo</h4>
        <div class="row">
          <div class="col-md-8">
            <h5>Your hand</h5>
            <form method="post" action="{{ url_for('play_card', game_id=game['id']) }}">
              <div class="hand">
                {% for c in your_hand %}
                <div>
                  <label style="cursor:pointer;">
                    <img src="{{ url_for('card_image', card_name=c) }}" class="card-image" alt="{{ c }}">
                    <div class="muted text-center">{{ card_labels[c] }}</div>
                    <div style="text-align:center;">
                      <input type="radio" name="card" value="{{ c }}">
                    </div>
                  </label>
                </div>
                {% endfor %}
              </div>
              <div class="mb-2">
                <label>Optional target (for TAKE / EXCHANGE / ACCUSATION / KILL): username</label>
                <input class="form-control" name="target_username" placeholder="target username (optional)">
              </div>
              <button class="btn btn-light">Play selected card</button>
            </form>
            <h6 class="mt-3">Game log</h6>
            <div class="game-log">
              {% for entry in game['log'] %}
                <div>{{ entry }}</div>
              {% endfor %}
            </div>
          </div>
          <div class="col-md-4">
            <h5>Players</h5>
            <ul>
              {% for p in game['players'] %}
                <li>{{ p }} {% if p not in game['alive'] %}(dead){% endif %} {% if game['killer'] == p %}(killer?) {% endif %}</li>
              {% endfor %}
            </ul>
            <a href="{{ url_for('play') }}" class="btn btn-light">Exit</a>
          </div>
        </div>
      </div>
      {% endblock %}
    """, user=user, game=g, your_hand=your_hand, card_labels=CARD_LABEL)

# ---------- Play card in a live game (simple demo engine) ----------
@app.route('/game/<game_id>/play_card', methods=['POST'])
def play_card(game_id):
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    uid = user['id']
    g = games.get(game_id)
    if not g or uid not in g['players']:
        flash("Game not found or you're not a player.")
        return redirect(url_for('play'))
    if uid not in g['alive']:
        flash("You're eliminated.")
        return redirect(url_for('game_room', game_id=game_id))
    card = request.form.get('card')
    target_username = request.form.get('target_username', '').strip()
    if not card or card not in g['hands'].get(uid, []):
        flash("Invalid card selection.")
        return redirect(url_for('game_room', game_id=game_id))

    # remove card from hand and put into used pile (some cards are "kept" like SHIELD but for demo we treat SHIELD as equipped)
    g['hands'][uid].remove(card)
    # apply simple effects demo:
    # STATE -> add to log
    if card == 'statecard':
        g['log'].append(f"{get_username_by_id(uid)} played STATE.")
        g['used'].append(card)
    elif card == 'searchcard':
        g['used'].append(card)
        # draw up to 3 cards from deck
        draws = []
        for _ in range(3):
            if not g['deck']:
                # reshuffle used into deck (simple)
                g['deck'] = g['used'].copy()
                g['used'] = []
                random.shuffle(g['deck'])
            if g['deck']:
                draws.append(g['deck'].pop())
        g['hands'][uid] += draws
        g['log'].append(f"{get_username_by_id(uid)} searched and drew {len(draws)} card(s).")
    elif card == 'shieldcard':
        # equip shield (kept until attempted kill). For demo leave shield out of 'used' and mark equipped
        g['shields_equipped'][uid] = True
        g['log'].append(f"{get_username_by_id(uid)} equipped SHIELD.")
    elif card == 'takecard':
        g['used'].append(card)
        if target_username:
            target_id = get_userid_by_username(target_username)
            if target_id and target_id in g['alive'] and target_id != uid:
                # take a random card from target if they have any (not equipped shield)
                target_hand = g['hands'].get(target_id, [])
                # if target has shield equipped, cannot take it; so treat shield equipped separately
                if target_hand:
                    taken = random.choice(target_hand)
                    target_hand.remove(taken)
                    g['hands'][uid].append(taken)
                    g['log'].append(f"{get_username_by_id(uid)} took a card from {target_username}.")
                else:
                    g['log'].append(f"{target_username} had no cards to take.")
            else:
                g['log'].append("Invalid target for TAKE.")
        else:
            g['log'].append("No target provided for TAKE.")
    elif card == 'exchangecard':
        g['used'].append(card)
        if target_username:
            target_id = get_userid_by_username(target_username)
            if target_id and target_id in g['alive'] and target_id != uid:
                # exchange entire hands (demo)
                g['hands'][uid], g['hands'][target_id] = g['hands'].get(target_id, []), g['hands'].get(uid, [])
                g['log'].append(f"{get_username_by_id(uid)} exchanged hands with {target_username}.")
            else:
                g['log'].append("Invalid target for EXCHANGE.")
        else:
            g['log'].append("No target provided for EXCHANGE.")
    elif card == 'judgecard':
        g['used'].append(card)
        g['log'].append(f"{get_username_by_id(uid)} played JUDGE — vote phase would start (demo).")
    elif card == 'accusatecard':
        # one-shot accusation: if correct kills killer; if wrong kill accuser
        g['used'].append(card)
        if target_username:
            target_id = get_userid_by_username(target_username)
            if target_id and target_id in g['alive']:
                # check if target is killer
                if g['killer'] == target_id:
                    # eliminate killer
                    g['alive'].remove(target_id)
                    g['used'] += g['hands'].get(target_id, [])
                    g['hands'][target_id] = []
                    g['log'].append(f"{get_username_by_id(uid)} used ACCUSATION and was correct! {target_username} eliminated.")
                    g['killer'] = None
                    g['finished'] = True
                    g['winner'] = "faithfuls"
                else:
                    # wrong accusation -> accuser dies
                    g['alive'].remove(uid)
                    g['used'] += g['hands'].get(uid, [])
                    g['hands'][uid] = []
                    g['log'].append(f"{get_username_by_id(uid)} used ACCUSATION and was wrong → {get_username_by_id(uid)} eliminated.")
            else:
                g['log'].append("Invalid target for ACCUSATION.")
        else:
            g['log'].append("No target provided for ACCUSATION.")
    elif card == 'killcard':
        # kill card can be used only if the player actually is the killer (demo allowance)
        # in this simplified demo, if you play killcard you become the killer and attempt to kill target
        g['used'].append(card)
        g['killer'] = uid
        if target_username:
            target_id = get_userid_by_username(target_username)
            if target_id and target_id in g['alive'] and target_id != uid:
                # if target has shield equipped, shield breaks and moves to killer (as per rules)
                if g['shields_equipped'].get(target_id):
                    # shield breaks and is taken by killer (move to killer hand)
                    g['shields_equipped'].pop(target_id, None)
                    g['hands'][uid].append('shieldcard')  # killer takes the shield card
                    g['log'].append(f"{get_username_by_id(uid)} attempted to kill {target_username} but shield broke and was taken by killer.")
                else:
                    # target dies: move their cards to used pile
                    g['alive'].remove(target_id)
                    g['used'] += g['hands'].get(target_id, [])
                    g['hands'][target_id] = []
                    g['log'].append(f"{get_username_by_id(uid)} used KILL and eliminated {target_username}.")
            else:
                g['log'].append("Invalid target for KILL.")
        else:
            g['log'].append("No target provided for KILL.")
    else:
        g['used'].append(card)
        g['log'].append(f"{get_username_by_id(uid)} played {card} (demo).")

    # Quick end checks
    if g['killer'] and winner_by_killer(g['alive'], g['killer']):
        g['finished'] = True
        g['winner'] = 'killer'
    elif g['killer'] is None and all((p in g['hands'] and g['hands'].get(p) == []) for p in g['players']):
        g['finished'] = True
        g['winner'] = 'faithfuls'
    return redirect(url_for('game_room', game_id=game_id))

# ---------- Helper small funcs ----------
def get_username_by_id(uid):
    db = get_db()
    cur = db.execute('SELECT username FROM users WHERE id = ?', (uid,))
    r = cur.fetchone()
    return r['username'] if r else f"UID{uid}"

def get_userid_by_username(username):
    db = get_db()
    cur = db.execute('SELECT id FROM users WHERE username = ?', (username,))
    r = cur.fetchone()
    return r['id'] if r else None

# ---------- Run ----------
if __name__ == '__main__':
    with app.app_context():
        init_db()
    # For debug/dev
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
