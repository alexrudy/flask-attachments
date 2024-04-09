=====
Usage
=====

To use Flask Attachments in a flask project::

    from sqlalchemy.orm import Base
    from flask import Flask
    import flask_attachments

    class MyBase(Base):
        __abstract__ = True

    app = Flask(__name__)
    attachments = flask_attachments.Attachments(app, registry=MyBase.metadata.registry)
