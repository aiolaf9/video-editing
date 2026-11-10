#!/usr/bin/env bash
# Audit kesiapan staging AI Video Studio di VPS (Hostinger KVM 2, Ubuntu) — HANYA MEMBACA.
#
# Tidak memasang, menghentikan, menyalakan, atau mengubah apa pun: tidak ada apt/systemctl start|stop|restart,
# tidak ada docker run/pull/compose up, tidak ada perubahan firewall/DNS/cron/berkas sistem. Satu-satunya keluaran
# adalah laporan di stdout. Lalu lintas jaringan keluar: hanya permintaan HEAD ke beberapa layanan publik yang
# dibutuhkan staging (unduhan model, paket, image) dan pembacaan sertifikat TLS domain yang disebut di bawah.
# Tidak mencetak isi variabel lingkungan, berkas konfigurasi aplikasi, atau argumen proses (bisa berisi rahasia).
#
# Jalankan sebagai root (atau sudo) agar nama proses pemilik port & status Docker terbaca; tanpa root tetap jalan
# dengan informasi lebih sedikit:
#   ssh root@SERVER 'bash -s' < deploy/audit/vps_audit.sh > audit-vps.txt
#   (PowerShell Windows) Get-Content deploy\audit\vps_audit.sh | ssh root@SERVER "tr -d '\r' | bash -s" > audit-vps.txt
#
# Opsi (variabel lingkungan): AUDIT_DOMAINS="editvideo.nurulhayat.org nurulhayat.org" (dicek DNS & TLS, hanya baca)
#                             AUDIT_PORTS="80 443 8001 18001 5432"            (port yang dicek bebas/terpakai)
#                             AUDIT_NET=0                                       (lewati semua uji jaringan keluar)
set -u
export LC_ALL=C
DOMAINS=${AUDIT_DOMAINS:-"editvideo.nurulhayat.org"}
PORTS=${AUDIT_PORTS:-"80 443 8001 18001 5432 6379 3306"}
NET=${AUDIT_NET:-1}
STAGING_SUBNET="172.31.250.0/24"     # subnet tetap jaringan "edge" di deploy/compose.staging.yml
T=15                                  # batas waktu per perintah (detik)

have() { command -v "$1" >/dev/null 2>&1; }
run() { timeout "$T" "$@" 2>&1 || echo "(gagal/tidak tersedia: $*)"; }
section() { printf '\n========== %s ==========\n' "$1"; }
kv() { printf '%-34s %s\n' "$1" "$2"; }
FLAGS=()
flag() { FLAGS+=("$1"); }

ROOT=0; [ "$(id -u)" = "0" ] && ROOT=1
echo "AUDIT STAGING AI VIDEO STUDIO — hanya baca"
kv "waktu (UTC)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
kv "dijalankan sebagai root" "$([ $ROOT = 1 ] && echo ya || echo 'tidak (sebagian info tidak terbaca)')"

section "1. Sistem"
[ -r /etc/os-release ] && . /etc/os-release && kv "OS" "${PRETTY_NAME:-?}"
kv "kernel" "$(uname -r)"
kv "virtualisasi" "$(have systemd-detect-virt && systemd-detect-virt || echo '?')"
kv "uptime / load" "$(uptime)"
kv "cgroup" "$(stat -fc %T /sys/fs/cgroup 2>/dev/null) (cgroup2fs = v2)"
kv "systemd" "$(systemctl --version 2>/dev/null | head -1)"
have timedatectl && kv "sinkron waktu" "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)"
[ -f /var/run/reboot-required ] && { kv "reboot tertunda" "YA"; flag "Reboot tertunda (paket kernel/sistem baru)"; }
kv "unattended-upgrades" "$(systemctl is-enabled unattended-upgrades 2>/dev/null || echo tidak-ada)"

section "2. CPU"
kv "nproc" "$(nproc)"
kv "model" "$(lscpu 2>/dev/null | awk -F: '/Model name/{gsub(/^ +/,"",$2);print $2;exit}')"
echo "--- vmstat 1 5 (kolom st = CPU dicuri hypervisor; us+sy = beban saat ini)"
run vmstat 1 5
steal=$(vmstat 1 3 2>/dev/null | tail -1 | awk '{print $NF}')
[ -n "${steal:-}" ] && [ "${steal:-0}" -ge 10 ] 2>/dev/null && flag "CPU steal ${steal}% (>= 10%): benchmark akan lebih lambat dari spesifikasi"
[ -r /proc/pressure/cpu ] && { echo "--- PSI cpu/memory/io"; cat /proc/pressure/cpu /proc/pressure/memory /proc/pressure/io 2>/dev/null; }

section "3. RAM & swap"
run free -m
run swapon --show
avail=$(free -m | awk '/^Mem:/{print $7}')
kv "RAM tersedia (MB)" "${avail:-?}"
[ -n "${avail:-}" ] && [ "$avail" -lt 5000 ] && flag "RAM tersedia ${avail} MB < 5000 MB: batas total staging ±3,9 GB + cadangan 1 GB tidak aman"
echo "--- 12 proses terbesar menurut RAM (nama perintah saja, tanpa argumen)"
ps -eo pid,user:12,comm,rss --sort=-rss 2>/dev/null | head -13 | awk 'NR==1{print;next}{printf "%-7s %-12s %-20s %6.0f MB\n",$1,$2,$3,$4/1024}'
kv "vm.swappiness" "$(cat /proc/sys/vm/swappiness 2>/dev/null)"
kv "vm.overcommit_memory" "$(cat /proc/sys/vm/overcommit_memory 2>/dev/null)"

section "4. Disk"
run df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay
run df -hi /
run lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
free_gb=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc 0-9)
kv "ruang kosong / (GB)" "${free_gb:-?}"
[ -n "${free_gb:-}" ] && [ "$free_gb" -lt 30 ] && flag "Disk kosong ${free_gb} GB < 30 GB (image ±2 GB + model + media sementara + log)"
if have docker && [ $ROOT = 1 ]; then
  droot=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null)
  [ -n "$droot" ] && kv "Docker root dir" "$droot ($(df -h --output=avail "$droot" 2>/dev/null | tail -1 | tr -d ' ') kosong)"
fi

section "5. Port & layanan yang sudah berjalan"
have ss || echo "(perintah ss tidak ada: daftar port tidak terbaca; coba: netstat -tulpn)"
if have ss; then
  echo "--- port TCP/UDP yang mendengar$([ $ROOT = 1 ] || echo ' (tanpa root: nama proses tidak tampil)')"
  ss -H -tulpn 2>/dev/null | awk '{printf "%-5s %-28s %s\n",$1,$5,$7}' | sort -u
  for p in $PORTS; do
    owner=$(ss -H -tlnp "( sport = :$p )" 2>/dev/null | awk '{print $4" "$6}' | head -2 | tr '\n' ' ')
    kv "port $p" "${owner:-bebas}"
  done
  ss -H -tln '( sport = :80 or sport = :443 )' 2>/dev/null | grep -q . && \
    flag "Port 80/443 sudah dipakai: Caddy staging tidak boleh mengikat 80/443 (pakai mode di belakang proxy/tunnel)"
fi
echo "--- layanan systemd yang berjalan"
systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | awk '{print $1}' | head -80
echo "--- panel hosting / web server yang terdeteksi"
for d in /usr/local/cpanel:cPanel /usr/local/CyberCP:CyberPanel /usr/local/psa:Plesk /www/server/panel:aaPanel \
         /usr/local/hestia:HestiaCP /usr/local/lsws:LiteSpeed /etc/nginx:nginx /etc/apache2:Apache2 /etc/httpd:httpd; do
  [ -e "${d%%:*}" ] && kv "terdeteksi" "${d##*:} (${d%%:*})"
done
have nginx && kv "nginx" "$(nginx -v 2>&1)"
have apache2 && kv "apache2" "$(apache2 -v 2>/dev/null | head -1)"
have caddy && kv "caddy (host)" "$(caddy version 2>/dev/null)"

section "6. Docker"
if have docker; then
  run docker version --format 'client {{.Client.Version}} / server {{.Server.Version}}'
  run docker compose version
  if [ $ROOT = 1 ] || id -nG | grep -qw docker; then
    run docker info --format 'driver cgroup={{.CgroupDriver}} v{{.CgroupVersion}} storage={{.Driver}} log={{.LoggingDriver}} root={{.DockerRootDir}} containers={{.Containers}} running={{.ContainersRunning}} images={{.Images}}'
    echo "--- container (nama, image, status, port)"
    docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | head -50
    echo "--- jaringan Docker & subnet"
    for n in $(docker network ls -q 2>/dev/null); do
      docker network inspect -f '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}' "$n" 2>/dev/null
    done
    # shellcheck disable=SC2046  # daftar ID jaringan memang dipecah per kata
    docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' $(docker network ls -q 2>/dev/null) 2>/dev/null \
      | grep -q "^${STAGING_SUBNET%/*}" && flag "Subnet $STAGING_SUBNET sudah dipakai jaringan Docker lain: ubah subnet edge staging"
    echo "--- compose project yang ada"
    docker compose ls 2>/dev/null
    run docker system df
    [ -r /etc/docker/daemon.json ] && { echo "--- /etc/docker/daemon.json (kunci saja)"; grep -oE '"[a-z-]+"[[:space:]]*:' /etc/docker/daemon.json | tr -d '":' | sort -u | tr '\n' ' '; echo; }
  else
    echo "(tanpa root/grup docker: status daemon tidak terbaca)"
  fi
else
  kv "docker" "TIDAK TERPASANG"
  flag "Docker belum terpasang: pemasangan Docker mengubah iptables & layanan sistem — butuh persetujuan & snapshot"
fi
echo "--- rute IPv4 (cek bentrok subnet $STAGING_SUBNET)"
ip -4 route 2>/dev/null
ip -4 route 2>/dev/null | grep -q "^${STAGING_SUBNET%.*}" && flag "Ada rute di $STAGING_SUBNET"

section "7. Firewall"
have ufw && { echo "--- ufw"; run ufw status verbose; }
have firewall-cmd && { echo "--- firewalld"; run firewall-cmd --state; }
if [ -r /etc/csf/csf.conf ]; then
  echo "--- CSF (kunci terpilih saja)"
  grep -E '^(TESTING|DOCKER|DOCKER_DEVICE|DOCKER_NETWORK4|TCP_IN|TCP_OUT|ETH_DEVICE) ' /etc/csf/csf.conf
  flag "CSF terdeteksi: restart CSF bisa menghapus aturan iptables Docker — perlu pengaturan DOCKER di csf.conf (persetujuan)"
fi
if [ $ROOT = 1 ]; then
  have iptables && kv "iptables (jumlah aturan)" "$(iptables -S 2>/dev/null | wc -l); rantai DOCKER: $(iptables -S 2>/dev/null | grep -c '^-N DOCKER')"
  have iptables && kv "kebijakan INPUT/FORWARD" "$(iptables -S 2>/dev/null | grep -E '^-P (INPUT|FORWARD)' | tr '\n' ' ')"
  have nft && kv "nftables (baris ruleset)" "$(nft list ruleset 2>/dev/null | wc -l)"
fi
have fail2ban-client && { echo "--- fail2ban"; run fail2ban-client status; }
kv "ip_forward" "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null) (Docker butuh 1)"
echo "(Firewall jaringan Hostinger di hPanel tidak terlihat dari dalam VPS: periksa manual.)"

section "8. Backup"
echo "Snapshot/backup Hostinger dikelola di hPanel dan tidak terlihat dari dalam VPS: periksa manual tanggal snapshot terakhir."
for t in restic borg rsnapshot duplicity rclone; do have "$t" && kv "alat backup" "$t"; done
[ -d /usr/local/jetapps ] && kv "alat backup" "JetBackup"
[ -e /var/cpanel/backups ] && kv "alat backup" "cPanel backup"
echo "--- jadwal cron (berkas & baris yang memuat backup/dump)"
ls -1 /etc/cron.d /etc/cron.daily /etc/cron.weekly 2>/dev/null | tr '\n' ' '; echo
if [ $ROOT = 1 ]; then
  for u in $(cut -d: -f1 /etc/passwd); do
    crontab -l -u "$u" 2>/dev/null | grep -iE 'backup|dump|rsync|restic|borg' | sed "s/^/[$u] /" | cut -c1-160
  done
fi
grep -rhiE 'backup|dump' /etc/cron.d 2>/dev/null | cut -c1-160

section "9. Jaringan keluar & domain"
kv "IPv4 lokal" "$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | tr '\n' ' ')"
for d in $DOMAINS; do
  kv "DNS $d" "$(getent ahostsv4 "$d" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')"
done
if [ "$NET" = "1" ] && have curl; then
  for u in https://huggingface.co https://pypi.org/simple/ https://registry-1.docker.io/v2/ https://github.com \
           https://deb.debian.org https://download.docker.com; do
    code=$(curl -s -o /dev/null -I --max-time 10 -w '%{http_code} %{time_total}s' "$u" 2>/dev/null || echo gagal)
    kv "keluar $u" "$code"
  done
  if have openssl; then
    for d in $DOMAINS; do
      cert=$(timeout 10 openssl s_client -connect "$d:443" -servername "$d" </dev/null 2>/dev/null \
             | openssl x509 -noout -subject -issuer -enddate 2>/dev/null | tr '\n' ' ')
      kv "TLS $d" "${cert:-tidak terbaca}"
    done
  fi
else
  echo "(uji jaringan keluar dilewati)"
fi

section "10. Keamanan dasar"
if [ -r /etc/ssh/sshd_config ]; then
  kv "sshd" "$(grep -hiE '^(PermitRootLogin|PasswordAuthentication|Port) ' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | tr '\n' ' ')"
fi
kv "akun dengan shell login" "$(grep -cvE '(nologin|false|sync|halt|shutdown)$' /etc/passwd 2>/dev/null)"
kv "ulimit -n" "$(ulimit -n)"
kv "user namespace" "$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo default)"

section "RINGKASAN"
kv "vCPU" "$(nproc)"
kv "RAM total / tersedia (MB)" "$(free -m | awk '/^Mem:/{print $2" / "$7}')"
kv "disk kosong / (GB)" "${free_gb:-?}"
if have docker; then
  dstate=$(docker info -f '{{.ServerVersion}}' 2>/dev/null)
  kv "Docker" "CLI terpasang; daemon: ${dstate:+aktif (versi $dstate)}${dstate:-tidak terjangkau / tidak berjalan}"
else
  kv "Docker" "tidak ada"
fi
kv "port 80/443" "$(ss -H -tln '( sport = :80 or sport = :443 )' 2>/dev/null | grep -q . && echo terpakai || echo bebas)"
if [ ${#FLAGS[@]} -eq 0 ]; then
  echo "Catatan: tidak ada tanda bahaya otomatis. Tetap tinjau seluruh laporan."
else
  echo "Perlu ditinjau:"
  for f in "${FLAGS[@]}"; do echo "  - $f"; done
fi
echo "Selesai. Skrip tidak mengubah apa pun di server."
