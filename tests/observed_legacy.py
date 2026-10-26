"""Isolated independent characterization of parent static evidence.

Never imported by commerce_core. These behaviors are historical defects/features,
not production policies, runtime verification of original code or auth guarantees.
"""
from decimal import Decimal, ROUND_HALF_UP


def observed_auto_save(items,mock_save,batch_size=1000):
    last_count=0
    for start in range(0,len(items),batch_size):
        response=mock_save(items[start:start+batch_size])
        if response['code']!=0:return 0
        last_count=response['data']['newCount']
    return last_count


def observed_desktop_fx(amount,destination,rates):
    amount=Decimal(amount)
    if destination in {'YN','₽'}:
        rate=Decimal(rates['CNY/RUB'])
    elif destination=='EUR':
        rate=(1/Decimal(rates['CNY/EUR'])).quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP)
    else:raise ValueError('Outside the supplied static characterization')
    return f"{(amount*rate).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP):.2f}"


def observed_extension_fx(amount,from_symbol,to_symbol,rates):
    symbols={'¥':'CNY','₽':'RUB','$':'USD','€':'EUR','₸':'KZT'}
    if from_symbol not in symbols or to_symbol not in symbols:return -1
    pair=symbols[from_symbol]+'/'+symbols[to_symbol]
    if pair not in rates:raise ValueError('Outside the supplied static characterization')
    return f"{(Decimal(amount)*Decimal(rates[pair])).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP):.2f}"


class ObservedLocalGate:
    def __init__(self,disk_expired=True):
        self.gate=False;self.expired=disk_expired;self.stop_invocations=0;self.events=[]
    def response(self,code):
        if code in {401,402} and not self.gate:
            self.gate=True;self.expired=True;self.stop_invocations+=1;self.events.append('request_auth_failure_once')
        elif code==0:
            self.gate=False;self.expired=False
        return {'gate':self.gate,'expired':self.expired}
    def non_login_allowed(self):return not self.gate
