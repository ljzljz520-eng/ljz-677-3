"""体检结果批量上传应用。"""
from flask import Flask

from . import config


def create_app() -> Flask:
    config.ensure_dirs()
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

    from . import db
    db.init_db()

    from .api import bp
    app.register_blueprint(bp)
    return app
