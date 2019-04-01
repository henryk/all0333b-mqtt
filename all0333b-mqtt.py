from paho.mqtt.client import Client
import json, time, yaml, re, threading, telnetlib



class All0333bMqtt:
    def __init__(self, config="config.yml"):
        self.config = yaml.load(open(config, 'r').read())
        self.config.setdefault('all0333b', {})
        for k, v in {
            'host': '172.16.1.254',
            'port': 23,
            'username': 'root',
            'password': 'admin'
        }.items():
            self.config['all0333b'].setdefault(k, v)
        self.config.setdefault('mqtt', {})
        for k, v in {
            "discovery_prefix": "homeassistant",
            "broker": "127.0.0.1",
            "port": 1883,
            "keepalive": 60,
            "password": "",
            "username": "homeassistant",
        }.items():
            self.config['mqtt'].setdefault(k, v)
        self.config.setdefault('object_id', 'all0333b')
        self.config.setdefault('name', 'DSL Upstream')
        self.config.setdefault('sensor', {})
        for k, v in {
            'update_interval': 20,
            'precision': 0,
        }.items():
            self.config['sensor'].setdefault(k, v)

        self.prefix = "/".join([
            self.config['mqtt']['discovery_prefix'],
            'sensor',
            re.sub(r'[^a-zA-Z0-9]', '_', "{}:{}".format(self.config['all0333b']['host'], self.config['all0333b']['port'])),
            self.config['object_id']
        ])

        self.connection = None
        self.mqttc = None
        self.state = {}
        self.disc_config = {}
        self.alive = True
        self.alive_condition = threading.Condition()
        self.last_pub = {}
        self.rates = {}

    def on_connect(self, *args, **kwargs):
        self.mqttc.publish("{}/config".format(self.prefix), json.dumps(self.disc_config), retain=True)

        self.mqttc.publish("{}/status".format(self.prefix), 'online', retain=True)

    def main(self):
        self.mqttc = Client()
        self.mqttc.will_set("{}/status".format(self.prefix), 'offline', retain=True)
        self.mqttc.enable_logger()
        self.mqttc.username_pw_set(self.config['mqtt']['username'], self.config['mqtt']['password'])
        self.mqttc.on_connect = self.on_connect

        self.disc_config = {
            'state_topic': "{}/state".format(self.prefix),
            #'expire_after': 10,
            'name': self.config['name'],
            'icon': 'mdi:wan',
            'availability_topic': "{}/status".format(self.prefix),
            'payload_available': "online",
            'payload_not_available': "offline",
            'json_attributes_topic': "{}/attributes".format(self.prefix),
            'force_update': self.config.get('force_update', False),
            'unique_id': '{}:{}'.format(self.config['all0333b']['host'], self.config['all0333b']['port']),
            'device': {
                'connections': [
                    ['tcp', '{}:{}'.format(self.config['all0333b']['host'], self.config['all0333b']['port'])],
                ],
                'identifiers':
                    ('{}:{}'.format(self.config['all0333b']['host'], self.config['all0333b']['port'])),
                'manufacturer': 'ALLNET',
                'model': 'ALL0333B',
            }
        }

        self.mqttc.connect(self.config['mqtt']['broker'], port=int(self.config['mqtt']['port']), keepalive=int(self.config['mqtt']['keepalive']))
        self.mqttc.loop_start()
        threading.Thread(target=self.connect_loop, daemon=True).start()

        while self.alive:
            self.alive = False

            with self.alive_condition:
                self.alive_condition.wait(self.config['sensor']['update_interval'] + 10)

    def connect_loop(self):
        try:
            while True:
                t = telnetlib.Telnet(self.config['all0333b']['host'], self.config['all0333b']['port'])
                self.handle_connection(t)
        finally:
            self.alive = False
            with self.alive_condition:
                self.alive_condition.notify_all()

    def handle_connection(self, tn):
        with tn:
            tn.read_until(b'login:')
            tn.read_very_eager()
            tn.write(self.config['all0333b']['username'].encode('us-ascii') + b'\n')
            self.alive = True

            tn.read_until(b'word:')
            tn.read_very_eager()
            tn.write(self.config['all0333b']['password'].encode('us-ascii') + b'\n')
            self.alive = True

            self.wait_for_prompt(tn)

            while True:
                self.query_ifconfig(tn)
                self.update_sensor()

                time.sleep(self.config['sensor']['update_interval'])

    def wait_for_prompt(self, tn):
        data = tn.read_until(b'# ')
        self.alive = True
        return data

    def query_ifconfig(self, tn):
        tn.read_very_eager()
        tn.write(b'ifconfig\n')
        self.alive = True

        now = time.time()
        data = self.wait_for_prompt(tn)
        have_interface = False
        next_line = False

        for line in data.splitlines():
            if next_line:
                next_line = False
                self.state['state'] = line.strip().split()[0].decode()

            if have_interface and line.strip().startswith(b'RX bytes'):
                have_interface = False
                parts = line.strip().split()
                self.update_rate('rx', now, int(parts[1].split(b':')[1]))
                self.update_rate('tx', now, int(parts[5].split(b':')[1]))

            if line.startswith(b'nas0'):
                have_interface = True
                next_line = True

    def update_rate(self, name, ts, value):
        r = self.rates.setdefault(name, {'ts': ts, 'lv': value, 'rate': None})
        if r['ts'] >= ts:
            r['rate'] = None
        else:
            diff = value - r['lv']
            if diff < 0:
                diff = diff + 4294967296
            r['rate'] = diff / (ts - r['ts'])
            self.state['{}_rate'.format(name)] = round(r['rate'], self.config['sensor']['precision'])
        r['ts'] = ts
        r['lv'] = value

    def update_sensor(self):
        pub = {
           'state': self.state.get('state'),
           'attributes': json.dumps({k:v for (k,v) in self.state.items() if not k == 'state'}),
        }
        for k, v in pub.items():
            if v != self.last_pub.get(k, None):
                self.mqttc.publish("{}/{}".format(self.prefix, k), v, retain=True)
                self.last_pub[k] = v
        self.alive = True


if __name__ == "__main__":
    All0333bMqtt().main()
