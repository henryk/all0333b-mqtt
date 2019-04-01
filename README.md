````
pip3 install -r requirements.txt
sudo loginctl enable-linger $(whoami)
systemctl --user enable $(pwd)/all0333b-mqtt.service

# Recommended: create and edit config.yml

systemctl --user start all0333b-mqtt.service
````

