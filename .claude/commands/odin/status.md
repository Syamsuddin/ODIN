Saat user menjalankan /odin:status, lakukan langkah berikut:

## Langkah 1 — Ambil Data

Panggil 2 tool MCP secara paralel:
1. `mcp__odin__server_info` — status server terkini
2. `mcp__odin__memory_digest` — load memory aktif (instruksi, profil server, profil user)

Jika salah satu gagal (misal server tidak terhubung), lanjutkan dengan data yang tersedia. Jangan error.

## Langkah 2 — Tampilkan Ringkasan

Format respons — **project harus baris PERTAMA**:

```
Project   : [project_name] → [ssh_target/alias] ([mode])
Server    : [hostname/OS info], uptime [uptime]
Disk      : [usage]%
Memory    : [used]/[total]
Services  : [daftar service aktif, misal: nginx ✓, php-fpm ✓, mysql ✓]

Ingatan   : [jumlah] entries aktif ([jumlah] instruksi)
```

Ambil `project_name` dari field `project_name` di response `server_info`.
Jika `project_name` kosong (mode legacy/v1), tampilkan tanpa baris Project:

```
Server    : [hostname] ([mode])
Uptime    : [uptime]
...
```

Jika server tidak terhubung, tampilkan:

```
Server    : tidak terhubung
            Pastikan SSH alias dan ODIN server sudah di-setup.
            Jalankan /odin:doctor untuk diagnostik.
```

## Langkah 3 — Siap

Akhiri dengan satu baris:

> Siap menerima perintah. Ketik apa saja tentang server Anda.

Jangan tambahkan penjelasan lain. Respons harus ringkas.
