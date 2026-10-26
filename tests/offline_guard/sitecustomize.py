"""Python audit-hook test guard, not an OS firewall or complete network sandbox."""
import ipaddress
import sys


def guard(event, args):
    if event in {'socket.connect','socket.sendto','socket.sendmsg'}:
        address = args[1]
        if isinstance(address, tuple):
            try:
                if ipaddress.ip_address(address[0]).is_loopback:
                    return
            except ValueError:
                pass
        raise PermissionError('OFFLINE_TEST_EXTERNAL_NETWORK_DENIED')
    if event in {'socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'}:
        host = args[0]
        if host not in ('127.0.0.1', '::1', 'localhost', None):
            raise PermissionError('OFFLINE_TEST_EXTERNAL_DNS_DENIED')
    if event == 'socket.getnameinfo':
        address=args[0]
        if not isinstance(address,tuple) or not ipaddress.ip_address(address[0]).is_loopback:
            raise PermissionError('OFFLINE_TEST_EXTERNAL_DNS_DENIED')


sys.addaudithook(guard)
