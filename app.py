import os
import secrets
import json
from functools import wraps
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, render_template, redirect, session, make_response

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(16)
app.config['JSON_AS_ASCII'] = False

# 从环境变量读取配置
API_TOKEN = os.environ.get('API_TOKEN', 'changeme')
ADMIN_PATH = os.environ.get('ADMIN_PATH', '/secret_panel')
DATA_DIR = os.environ.get('DATA_DIR', '.')

DATA_FILE = os.path.join(DATA_DIR, 'proxies.json')
ALIAS_FILE = os.path.join(DATA_DIR, 'aliases.json')
EVENT_LOG_FILE = os.path.join(DATA_DIR, 'events.log')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

proxies = {}
aliases = {}
config = {"timezone": int(os.environ.get('TIMEZONE', 8))}

def load_data():
    global proxies, aliases, config
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                proxies = json.load(f)
        except:
            proxies = {}
    if os.path.exists(ALIAS_FILE):
        try:
            with open(ALIAS_FILE, 'r') as f:
                aliases = json.load(f)
        except:
            aliases = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        except:
            pass

def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump(proxies, f, indent=2, ensure_ascii=False)

def save_aliases():
    with open(ALIAS_FILE, 'w') as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)

def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def get_display_name(instance_id):
    return aliases.get(instance_id, instance_id)

def get_now():
    tz = timezone(timedelta(hours=config.get("timezone", 8)))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

def log_event(msg):
    with open(EVENT_LOG_FILE, 'a') as f:
        f.write(f"{get_now()} | {msg}\n")

def get_events():
    if os.path.exists(EVENT_LOG_FILE):
        with open(EVENT_LOG_FILE, 'r') as f:
            return f.readlines()[-100:]
    return []

load_data()

def check_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization') or request.args.get('key')
        if token == API_TOKEN:
            return f(*args, **kwargs)
        if session.get('logged_in'):
            return f(*args, **kwargs)
        if request.cookies.get('auth_token') == API_TOKEN:
            return f(*args, **kwargs)
        if request.path.startswith('/api/'):
            return jsonify({"error": "Forbidden"}), 403
        else:
            return redirect(ADMIN_PATH + '/login')
    return decorated_function

@app.route('/')
def index():
    return "System Online", 200

@app.route(ADMIN_PATH + '/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('token')
        if pwd == API_TOKEN:
            session['logged_in'] = True
            resp = make_response(redirect(ADMIN_PATH))
            resp.set_cookie('auth_token', API_TOKEN, max_age=86400*30)
            return resp
        else:
            return render_template('login.html', error="无效的密钥")
    return render_template('login.html')

@app.route(ADMIN_PATH)
@check_auth
def admin_ui():
    return render_template('index.html', proxies=proxies, aliases=aliases, config=config)

@app.route(ADMIN_PATH + '/logs')
@check_auth
def logs_page():
    return render_template('logs.html', logs=get_events())

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    resp = make_response(redirect(ADMIN_PATH + '/login'))
    resp.delete_cookie('auth_token')
    return resp

@app.route('/api/report', methods=['POST'])
def report():
    if request.headers.get('Authorization') != API_TOKEN:
        return jsonify({"error": "Forbidden"}), 403
    data = request.json
    if not data or 'id' not in data:
        return jsonify({"error": "Missing ID"}), 400
    instance_id = str(data['id'])
    new_ip = data.get('ip', 'unknown')
    new_status = data.get('status', 'online')
    new_tier = data.get('tier', 'UNKNOWN')
    old_ip = proxies.get(instance_id, {}).get('ip', None)
    display_name = get_display_name(instance_id)
    if new_status == "changing":
        log_event(f"开始换IP: {display_name}")
    elif old_ip and old_ip != new_ip:
        log_event(f"IP变更完成: {display_name} | {old_ip} -> {new_ip} ({new_tier})")
    elif not old_ip:
        log_event(f"新节点上线: {display_name} | {new_ip} ({new_tier})")
    proxies[instance_id] = {
        "id": instance_id, "ip": new_ip, "socks_port": data.get('socks_port'),
        "http_port": data.get('http_port'), "user": data.get('user'),
        "pass": data.get('pass'), "region": data.get('region'),
        "tier": new_tier, "status": new_status, "last_seen": get_now(),
    }
    save_data()
    return jsonify({"status": "ok"})

@app.route('/api/list', methods=['GET'])
@check_auth
def list_proxies():
    result = []
    for id, p in proxies.items():
        item = dict(p)
        item['alias'] = aliases.get(id, '')
        if p.get('status') == 'changing':
            item['note'] = 'IP更换中'
        result.append(item)
    return jsonify(result)

@app.route('/api/rename', methods=['POST'])
@check_auth
def rename_instance():
    data = request.json
    instance_id = data.get('id')
    new_name = data.get('name', '').strip()
    if not instance_id:
        return jsonify({"error": "Missing ID"}), 400
    if new_name:
        aliases[instance_id] = new_name
    else:
        aliases.pop(instance_id, None)
    save_aliases()
    return jsonify({"status": "ok"})

@app.route('/api/config', methods=['GET', 'POST'])
@check_auth
def api_config():
    global config
    if request.method == 'POST':
        data = request.json
        if 'timezone' in data:
            config['timezone'] = int(data['timezone'])
        save_config()
        return jsonify({"status": "ok", "config": config})
    return jsonify(config)

@app.route('/api/refresh', methods=['POST'])
@check_auth
def trigger_refresh():
    data = request.json
    tier = data.get('tier', 'standard').lower()
    if tier not in ['standard', 'premium', 'toggle']:
        tier = 'standard'
    targets = []
    if data.get('all'):
        targets = list(proxies.values())
    elif 'ids' in data and isinstance(data['ids'], list):
        for tid in data['ids']:
            if tid in proxies:
                targets.append(proxies[tid])
    elif 'id' in data and data['id'] in proxies:
        targets = [proxies[data['id']]]
    if not targets:
        return jsonify({"error": "No targets found"}), 404
    results = []
    import requests as req_lib
    for p in targets:
        worker_ip = p.get('ip')
        worker_id = p.get('id', 'unknown')
        display_name = get_display_name(worker_id)
        try:
            url = f"http://{worker_ip}:4444/refresh?key={API_TOKEN}&tier={tier}"
            req_lib.post(url, timeout=2)
            results.append({"id": worker_id, "alias": display_name, "status": "sent", "tier": tier})
            log_event(f"发送刷新指令: {display_name} ({worker_ip}) [{tier}]")
        except:
            results.append({"id": worker_id, "alias": display_name, "status": "sent_timeout", "tier": tier})
            log_event(f"刷新指令已发(超时): {display_name} ({worker_ip}) [{tier}]")
    return jsonify({"results": results})

@app.route('/api/events', methods=['GET'])
@check_auth
def api_events():
    return jsonify({"logs": get_events()})

@app.route('/api/docs', methods=['GET'])
@check_auth
def api_docs():
    return jsonify({
        "meta": {"name": "代理控制台 API", "version": "1.3", "auth": "Header 'Authorization: <TOKEN>' 或 Query '?key=<TOKEN>'"},
        "endpoints": [
            {"path": "GET /api/list", "desc": "获取所有代理节点列表", "response_example": [{"id": "123", "alias": "node1", "ip": "1.2.3.4", "socks_port": 10086, "http_port": 10010, "user": "u", "pass": "p", "region": "us-west1", "tier": "STANDARD", "status": "online", "last_seen": "2025-11-28 12:00:00"}]},
            {"path": "POST /api/refresh", "desc": "触发节点换IP", "body_params": {"id": "单个ID", "ids": ["ID1", "ID2"], "all": True, "tier": "standard|premium|toggle"}, "response_example": {"results": [{"id": "123", "alias": "node1", "status": "sent", "tier": "premium"}]}},
            {"path": "POST /api/config", "desc": "修改配置", "body_params": {"timezone": 8}, "response_example": {"status": "ok", "config": {"timezone": 8}}},
            {"path": "GET /api/events", "desc": "获取事件日志", "response_example": {"logs": ["2025-11-28 12:00:00 | 节点上线: node1"]}}
        ]
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
