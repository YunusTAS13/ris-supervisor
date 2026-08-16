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

`test/sandbox-test.sh` runs the whole thing in a scratch directory without
touching the system: boot services, crash-restart, backoff limiter, graceful
stop, and a clean SIGTERM shutdown of the daemon itself.

```bash
bash test/sandbox-test.sh
```

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
- Early development stage. Test in a VM/QEMU, not on your main machine.

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

`test/sandbox-test.sh` her şeyi geçici bir dizinde, sisteme dokunmadan uçtan uca
sınar: boot servisleri, crash-restart, backoff sınırlayıcı, nazik durdurma ve
daemon'un kendisinin temiz SIGTERM kapanışı.

```bash
bash test/sandbox-test.sh
```

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
- Erken geliştirme aşaması. Asıl makinenizde değil, VM/QEMU'da test edin.

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