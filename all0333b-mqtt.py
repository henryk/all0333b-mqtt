from paho.mqtt.client import Client
import json, time, yaml, re, threading, telnetlib, traceback

# Source: https://svn.dd-wrt.com//browser/src/linux/universal/linux-3.2/drivers/net/ethernet/ifxatm/include/drv_dsl_cpe_api.h?rev=18222
LINE_STATES = {
    0x00000000: "NOT_INITIALIZED",
    0x00000001: "EXCEPTION",
    0x00000010: "NOT_UPDATED",
    0x000000FF: "IDLE_REQUEST",
    0x00000100: "IDLE",
    0x000001FF: "SILENT_REQUEST",
    0x00000200: "SILENT",
    0x00000300: "HANDSHAKE",
    0x00000310: "BONDING_CLR",
    0x00000380: "FULL_INIT",
    0x000003C0: "SHORT_INIT_ENTRY",
    0x00000400: "DISCOVERY",
    0x00000500: "TRAINING",
    0x00000600: "ANALYSIS",
    0x00000700: "EXCHANGE",
    0x00000800: "SHOWTIME_NO_SYNC",
    0x00000801: "SHOWTIME_TC_SYNC",
    0x00000900: "FASTRETRAIN",
    0x00000A00: "LOWPOWER_L2",
    0x00000B00: "LOOPDIAGNOSTIC_ACTIVE",
    0x00000B10: "LOOPDIAGNOSTIC_DATA_EXCHANGE",
    0x00000B20: "LOOPDIAGNOSTIC_DATA_REQUEST",
    0x00000C00: "LOOPDIAGNOSTIC_COMPLETE",
    0x00000D00: "RESYNC",
    0x01000000: "TEST",
    0x01000001: "TEST_LOOP",
    0x01000010: "TEST_REVERB",
    0x01000020: "TEST_MEDLEY",
    0x01000030: "TEST_SHOWTIME_LOCK",
    0x01000040: "TEST_QUIET",
    0x02000000: "LOWPOWER_L3",
    0x03000000: "UNKNOWN",
}


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
        self.state_lock = threading.Lock()
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
                #t.set_debuglevel(5)
                self.handle_connection(t)
        except:
            traceback.print_exc()
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
                self.query_line_state(tn)
                with self.state_lock:
                    self.update_sensor()

                time.sleep(self.config['sensor']['update_interval'])

    def wait_for_prompt(self, tn):
        data = tn.read_until(b'# ')
        self.alive = True
        return data

    def exec_command(self, tn, command):
        tn.read_very_eager()
        tn.write(command)
        tn.read_very_eager()
        tn.write(b"\n")
        return self.wait_for_prompt(tn)

    def call_dsl_cpe_pipe(self, tn, command, *args):
        command = ['dsl_cpe_pipe.sh', command] + list(map(str, args))

        data = self.exec_command(tn, " ".join(command).encode())
        last_line = [l for l in data.strip().splitlines() if b'=' in l][-1].decode()

        return dict(e.split("=", 1) for e in last_line.split())

    def query_line_state(self, tn):
        d = self.call_dsl_cpe_pipe(tn, "lsg")

        self.state['state'] = LINE_STATES.get(int(d['nLineState'], 0), 'UNKNOWN')

    def query_ifconfig(self, tn):
        tn.read_very_eager()
        tn.write(b'ifconfig\n')
        self.alive = True

        now = time.time()
        data = self.wait_for_prompt(tn)
        have_interface = False

        for line in data.splitlines():
            if have_interface and line.strip().startswith(b'RX bytes'):
                have_interface = False
                parts = line.strip().split()
                self.update_rate('rx', now, int(parts[1].split(b':')[1]))
                self.update_rate('tx', now, int(parts[5].split(b':')[1]))

            if line.startswith(b'nas0'):
                have_interface = True

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
