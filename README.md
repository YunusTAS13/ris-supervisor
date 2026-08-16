# ris-supervisor

The service starter/manager companion for **RIS** — the [modular init system for
GNU/Linux](https://codeberg.org/javav12/ris) · **RIS** için servis
başlatıcı/yönetici uydusu.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

```
 KERNEL ──▶ /init (prepare.sh) ──▶ RIS (PID 1)
                                       │
                          spawns child│
                                       ▼
                                  rissup (this project)
                               ┌──────────┬──────────┬──────────┐
                               │  ticker  │ dropbear │  getty   │ ...
                               └──────────┴──────────┴──────────┘
                               ▲
                           risctl (unix socket, /var/run/ris-svc.sock)
```

---

## English

RIS deliberately does not ship a service manager; it delegates that job to an
external binary. `rissup` is that binary: it supervises services, applies
restart policies, logs their output, and shuts everything down cleanly when RIS
asks for it. `risctl` is its control client. `ris-supervisor` is an independent
companion, **not a fork** of RIS — the only change RIS needs is a single
`spawn` line.

### The RIS contract

RIS treats the service starter as a plain long-lived child process:

| RIS behaviour                        | What `rissup` must do                              |
| ------------------------------------ | -------------------------------------------------- |
| `fork+exec` at boot (`src/main.py`)  | run forever as a direct child of PID 1             |
| reaps zombies, detects starter death | never die on transient failures                    |
| respawns starter with backoff        | restart cleanly, or don't die in the first place   |
| `SIGTERM`, waits 30s, then `SIGKILL` | stop all services and exit **within 30 seconds**   |

`rissup` satisfies the last point by sending `SIGTERM` to every service on
shutdown, giving each its `stop_timeout` (default 10s) before escalating to
`SIGKILL`, and exiting immediately once everything is stopped.

RIS's own FIFO only understands `rl0`/`rl6`. Runlevel 1–5 are reserved "for the
service starter" — that is where the `risctl` control socket lives.

### Install & integrate

Build a musl binary the same way RIS does:

```bash
sudo ./build.sh          # produces ./ris-musl (static, via alpine container)
sudo cp ./ris-musl /sbin/rissup
sudo cp examples/*.service /etc/ris/services/
```

Make RIS spawn this instead of a shell. In RIS's `src/main.py`, inside
`service_starter_spawn`:

```python
service_starter_pid = spawn("/sbin/rissup", ["rissup"])
```

That is the whole integration — no pipes, no IPC with RIS, just a child process
that behaves.

### Service definitions

Every `*.service` file in `/etc/ris/services/` describes one service. Lines are
`key = value`, `#` starts a comment, and `[Service]` section headers are
ignored.

```ini
[Service]
# the command to run (split like a shell)
exec = /usr/sbin/dropbear -E -F
# restart policy: always | on_failure | never
restart = always
# wait before respawning a crashed service (seconds)
restart_delay = 2
# give up after this many consecutive fast failures (0 = unlimited)
backoff_limit = 10
# how long a service may take to die before SIGKILL (seconds)
stop_timeout = 5
# start automatically when rissup boots
run_at_boot = true
# working directory and umask for the service
chdir = /
umask = 022
# drop privileges (needs root)
# user = nobody
# group = nogroup
# extra environment variables
environment = FOO=bar BAZ=qux
# what to do with the service's stdio:
#   log     -> stdin /dev/null, stdout/stderr to /var/log/ris/<name>.log (default)
#   none    -> keep the inherited console fds (interactive shells, e.g. console.service)
#   devnull -> send stdin/stdout/stderr to /dev/null
redirect = log
```

A service is considered stable after staying up for 5 seconds; its fast-failure
counter then resets. Output goes to `/var/log/ris/<name>.log`.

### Control

```bash
risctl status                # name pid state restarts last-exit
risctl start  getty          # start one service
risctl stop   getty          # stop one service
risctl restart getty         # restart one service
risctl reload                # re-read /etc/ris/services, start new boot services
risctl list                  # same as status
```

Commands travel over a line-based unix socket at `/var/run/ris-svc.sock`, one
command per connection — the same "do one thing" philosophy as RIS's FIFO.

### Test

We recommend testing in three stages — start safe, and go real only last:

1. **Sandbox** — `bash test/sandbox-test.sh` runs the supervision logic in a
   scratch directory without touching the system (boot services,
   crash-restart, backoff limiter, graceful stop, clean shutdown). Nothing
   here is real PID 1.
2. **QEMU/VM** — `test/build-rootfs.sh` builds a reference initramfs and
   `test/vm-run.sh` boots it, so RIS runs as the real init in a VM. This is
   where the mounts, devtmpfs, console and shutdown path actually run — all
   invisible in the sandbox.
3. **Real hardware** — only on a spare machine, never your main one. pre-beta
   PID 1 can hang a system, and the shutdown path is hardware-dependent.

### Lint

```bash
ruff check
```

### Limitations

- `rissup` is for systems where RIS is the real PID 1. Running it as a regular
  process still works for supervision testing (see the sandbox test), but
  privilege dropping (`user`/`group`) needs root.
- If `rissup` is killed and RIS respawns it, previously running services are
  reparented to PID 1 and are no longer supervised; they keep running until
  shutdown. The new instance starts the `run_at_boot` set fresh.
- Early development stage. Follow the three-stage testing guide in the "Test"
  section: sandbox first, then QEMU; real hardware only on a spare machine.

### Roadmap

Landing next (work in progress):

- `redirect = log | none | devnull` per-service stdio policy — already in
  `examples/console.service`, which gives an interactive login shell on the
  VM console via `cttyhack` + `redirect = none`.
- `test/build-rootfs.sh`: a reference initramfs rootfs builder that assembles
  RIS + rissup + risctl (PyInstaller `--onedir`, musl/alpine when Docker is
  available, glibc otherwise) with a static BusyBox, patches RIS to spawn
  `/sbin/rissup`, and packs `initramfs.cpio.gz`.
- `test/vm-run.sh` + `test/vm-test.md`: a QEMU boot smoke test
  (`qemu-system-x86_64 -kernel ... -initrd ... -nographic`) with a
  step-by-step verification walkthrough (console login, `risctl status`,
  kill/auto-restart, clean shutdown).

### License & credits

Licensed under the **GNU GPL v3** (or later), matching RIS.

This project exists because of the **RIS** init system by
[javav12](https://codeberg.org/javav12) — a modular PID 1 for GNU/Linux that
deliberately leaves the *service starter* slot open and delegates service
management to an external binary. Big thank you to **javav12** for building RIS,
for that clean "do one thing and do it well" philosophy, and for the space this
project plugs into.

- RIS project: <https://codeberg.org/javav12/ris>
- Built in the style of RIS: same layout, build pipeline (PyInstaller
  `--onedir`, musl/alpine & debian container builds) and `ruff select ALL`.

---

## Türkçe

RIS servis yöneticisini bilinçli olarak yanında taşımaz; bu işi harici bir
binary'e devreder. İşte `rissup` o binary: servisleri süpervize eder, restart
politikalarını uygular, çıktılarını loglar ve RIS kapanış istediğinde her şeyi
temiz biçimde durdurur. `risctl` de onun kontrol istemcisidir. `ris-supervisor`
bağımsız bir uydu projedir, **RIS'in forku değildir** — RIS tarafında gereken tek
değişiklik tek satırlık bir `spawn` çağrısıdır.

### RIS sözleşmesi

RIS, servis başlatıcısını sıradan uzun ömürlü bir çocuk süreç olarak görür:

| RIS davranışı                        | `rissup`'ın yapması gereken                      |
| ------------------------------------ | ------------------------------------------------ |
| boot'ta `fork+exec` (`src/main.py`)  | PID 1'in doğrudan çocuğu olarak sonsuza dek çalış |
| zombileri toplar, ölümü fark eder    | geçici arızalarda asla ölme                      |
| starter'ı backoff ile yeniden doğurur| temiz restart et ya da zaten ölme               |
| `SIGTERM`, 30s bekler, sonra `SIGKILL`| tüm servisleri durdur ve **30 saniye içinde** çık |

`rissup` son maddeyi şöyle sağlıyor: kapanışta her servise `SIGTERM` gönderir,
her birine `stop_timeout` (varsayılan 10s) tanır, sonra `SIGKILL`'e yükseltir ve
her şey durur durmaz çıkar.

RIS'in FIFO'su yalnızca `rl0`/`rl6` anlar; 1–5. runlevel'ler "service starter
için" ayrılmıştır — `risctl`'in kontrol socket'i tam da oraya kurulur.

### Kurulum ve entegrasyon

RIS ile aynı yöntemle bir musl binary derleyin:

```bash
sudo ./build.sh          # ./ris-musl üretir (static, alpine container üzerinden)
sudo cp ./ris-musl /sbin/rissup
sudo cp examples/*.service /etc/ris/services/
```

RIS'in bu shell yerine bunu doğurmasını sağlayın. RIS'in `src/main.py` içinde,
`service_starter_spawn` fonksiyonunda:

```python
service_starter_pid = spawn("/sbin/rissup", ["rissup"])
```

Entegrasyon bu kadar — pipe yok, RIS ile IPC yok, yalnızca düzgün davranan bir
çocuk süreç.

### Servis tanımları

`/etc/ris/services/` içindeki her `*.service` dosyası bir servisi anlatır.
Satırlar `anahtar = değer` biçimindedir, `#` yorum başlatır, `[Service]` bölüm
başlıkları yok sayılır.

```ini
[Service]
# çalıştırılacak komut (shell gibi bölünür)
exec = /usr/sbin/dropbear -E -F
# restart politikası: always | on_failure | never
restart = always
# çöken servisi yeniden başlatmadan önce bekleme (saniye)
restart_delay = 2
# bu kadar üst üste hızlı hatadan sonra vazgeç (0 = sınırsız)
backoff_limit = 10
# servisin SIGKILL'den önce durması için tanınan süre (saniye)
stop_timeout = 5
# rissup boot olurken otomatik başlasın mı
run_at_boot = true
# servisin çalışma dizini ve umask değeri
chdir = /
umask = 022
# ayrıcalık düşürme (root gerektirir)
# user = nobody
# group = nogroup
# ekstra ortam değişkenleri
environment = FOO=bar BAZ=qux
# servisin stdio'su ne yapsın:
#   log     -> stdin /dev/null, stdout/stderr /var/log/ris/<ad>.log (varsayılan)
#   none    -> konsoldan miras alınan fd'ler kalsın (etkileşimli kabuklar, ör. console.service)
#   devnull -> stdin/stdout/stderr /dev/null'a gitsin
redirect = log
```

Bir servis 5 saniye ayakta kalırsa "istikrarlı" sayılır ve hızlı-hata sayacı
sıfırlanır. Çıktı `/var/log/ris/<ad>.log` dosyasına yazılır.

### Kontrol

```bash
risctl status                # ad pid durum restart son-çıkış
risctl start  getty          # bir servisi başlat
risctl stop   getty          # bir servisi durdur
risctl restart getty         # bir servisi yeniden başlat
risctl reload                # /etc/ris/services'ı yeniden oku, yeni boot servislerini başlat
risctl list                  # status ile aynı
```

Komutlar `/var/run/ris-svc.sock` üzerindeki satır tabanlı bir unix
socket'inden geçer — her bağlantıda bir komut; RIS'in FIFO'suyla aynı
"tek iş, iyi iş" felsefesi.

### Test

Denemeyi üç aşamada yapmanı öneririz — önce güvenli, en son gerçek donanım:

1. **Sandbox** — `bash test/sandbox-test.sh` süpervizyon mantığını geçici bir
   dizinde, sisteme dokunmadan uçtan uca sınar: boot servisleri, crash-restart,
   backoff sınırlayıcı, nazik durdurma ve daemon'un temiz kapanışı. Burada
   gerçek PID 1 yoktur.
2. **QEMU/VM** — `test/build-rootfs.sh` referans initramfs'i kurar,
   `test/vm-run.sh` onu açar, böylece RIS bir VM'de gerçek init olarak çalışır.
   Mount'lar, devtmpfs, konsol ve kapanış akışı işte tam burada gerçekten
   çalışır — bunların hepsi sandbox'ta görünmez.
3. **Gerçek donanım** — yalnızca yedek bir makinede, asla ana makinede.
   Pre-beta PID 1 sistemi kilitleyebilir ve kapanış akışı donanıma bağlıdır.

### Lint

```bash
ruff check
```

### Sınırlamalar

- `rissup`, RIS'in gerçek PID 1 olduğu sistemler içindir. Sıradan bir süreç
  olarak çalıştırmak yine de süpervize testi için işe yarar (sandbox testine
  bakın), ama `user`/`group` ile ayrıcalık düşürme root gerektirir.
- Eğer `rissup` öldürülür ve RIS onu yeniden doğurursa, önceden çalışan
  servisler PID 1'e reparent edilir ve artık süpervize edilmez; kapanana dek
  çalışmaya devam ederler. Yeni örnek, `run_at_boot` kümesini taze başlatır.
- Erken geliştirme aşaması. "Test" bölümündeki üç aşamalı test rehberini izle:
  önce sandbox, sonra QEMU; gerçek donanım yalnızca yedek makinede.

### Yakın plan (Roadmap)

Sıradakiler yolda (çalışmalar sürüyor):

- `redirect = log | none | devnull` — servis bazında stdio politikası; zaten
  `examples/console.service` içinde. Bu servis, `cttyhack` + `redirect = none`
  ile VM konsolunda etkileşimli bir login kabuğu verir.
- `test/build-rootfs.sh`: referans initramfs rootfs builder. RIS + rissup +
  risctl'i (PyInstaller `--onedir`; Docker varsa musl/alpine, yoksa glibc)
  statik BusyBox ile birleştirir, RIS'i `/sbin/rissup`'ı spawn edecek şekilde
  yamalar ve `initramfs.cpio.gz` paketler.
- `test/vm-run.sh` + `test/vm-test.md`: QEMU boot duman testi
  (`qemu-system-x86_64 -kernel ... -initrd ... -nographic`) ve adım adım
  doğrulama yönergesi (konsol girişi, `risctl status`, kill/otomatik yeniden
  başlatma, temiz kapanış).

### Lisans ve teşekkür

**GNU GPL v3** (veya sonrası) lisansıyla dağıtılır — RIS ile uyumlu.

Bu proje, [javav12](https://codeberg.org/javav12) tarafından yazılan **RIS** init
sistemi sayesinde var oldu — *service starter* yuvasını bilinçli olarak boş
bırakan ve servis yönetimini harici bir binary'e devreden modüler bir GNU/Linux
PID 1'i. **javav12**'ye RIS'i yaptığı, o temiz "do one thing and do it well"
felsefesi ve bu projenin oturduğu boş yuva için kocaman teşekkürler.

- RIS projesi: <https://codeberg.org/javav12/ris>
- RIS'in tarzında inşa edildi: aynı dizin düzeni, aynı build zinciri
  (PyInstaller `--onedir`, musl/alpine & debian container build'leri) ve aynı
  `ruff select ALL` lint ayarı.