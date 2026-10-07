#!/bin/sh
set -e
sudo apt-get remove --no-auto-remove dpkg-repack libbrotli-dev libbz2-dev libdbus-1-dev libdrm-dev libegl-dev libepoxy-dev libfontenc-dev libfreetype-dev libgbm-dev libpciaccess-dev libpixman-1-dev libpng-dev libprotobuf-c-dev libudev-dev libxcvt-dev libxfont-dev libxkbfile-dev libxshmfence-dev mesa-common-dev protobuf-c-compiler
sudo dpkg -i /home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/p06-server-preparation/rollback/libfreetype6_2.13.2+dfsg-1ubuntu0.1_amd64.deb
