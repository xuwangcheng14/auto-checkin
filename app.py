#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动签到管理平台 - 入口

用法:
    python app.py                 # 默认 http://127.0.0.1:7777
    python app.py --port 8080
    python app.py --host 0.0.0.0  # 允许局域网访问(注意: Web 界面无鉴权)
"""
import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from flask import Flask, send_from_directory  # noqa: E402

from api.routes import bp  # noqa: E402
from core import db, scheduler  # noqa: E402

WEB_DIR = os.path.join(BASE_DIR, "web")


def create_app():
    app = Flask(__name__)
    db.init_db()
    app.register_blueprint(bp)

    @app.get("/")
    def index():
        resp = send_from_directory(WEB_DIR, "index.html")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/assets/<path:filename>")
    def assets(filename):
        return send_from_directory(os.path.join(WEB_DIR, "assets"), filename)

    scheduler.start()
    return app


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="自动签到管理平台")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7777")))
    args = parser.parse_args()
    print("自动签到管理平台已启动: http://%s:%d" % (args.host, args.port))
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
