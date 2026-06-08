"""Route blueprints registration."""

from flask import Flask


def register_blueprints(app: Flask):
    from routes.kb import kb_bp
    from routes.index import index_bp
    from routes.search import search_bp
    from routes.chat import chat_bp
    from routes.audit import audit_bp
    from routes.material import material_bp
    from routes.config_api import config_bp
    from routes.analysis import analysis_bp

    app.register_blueprint(kb_bp)
    app.register_blueprint(index_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(material_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(analysis_bp)
