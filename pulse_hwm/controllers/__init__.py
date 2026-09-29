"""UI-thread controllers: the glue between background services and the UI.

Each controller is a QObject that lives on the UI thread. That matters:
Qt delivers a cross-thread signal to a QObject's slot on the thread the
object LIVES on (queued). A signal connected to a plain function or lambda
instead runs on whatever thread emitted it — which is how worker-thread
code used to end up touching widgets. app.py only builds these and connects
them; the logic lives here where it can be tested with fakes.
"""
