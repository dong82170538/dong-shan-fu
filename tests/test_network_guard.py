"""Synthetic audit events: these tests never send an external datagram."""
import sys
import unittest
from tests.offline_guard import sitecustomize

class AuditGuardTests(unittest.TestCase):
    def test_tcp_udp_and_sendmsg_external_events_are_denied_without_io(self):
        for event in ('socket.connect','socket.sendto','socket.sendmsg'):
            with self.assertRaises(PermissionError):sys.audit(event,object(),('203.0.113.10',9))
            sys.audit(event,object(),('127.0.0.1',9))
            sys.audit(event,object(),('::1',9))
    def test_direct_dns_audit_events_are_guarded_without_lookup(self):
        for event in ('socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'):
            with self.assertRaises(PermissionError):sys.audit(event,'fixture.invalid')
            sys.audit(event,'localhost')
        with self.assertRaises(PermissionError):sys.audit('socket.getnameinfo',('203.0.113.10',9),0)
        sys.audit('socket.getnameinfo',('127.0.0.1',9),0)
