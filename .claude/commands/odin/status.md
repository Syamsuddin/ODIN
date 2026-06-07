Saat user menjalankan /odin:status, lakukan langkah berikut:

## Langkah 1 — Ambil Data

Panggil 2 tool MCP secara paralel:
1. `mcp__odin__memory_digest` — load memory aktif (instruksi, profil server, profil user)
2. `mcp__odin__server_info` — status server terkini (disk, memory, uptime)

Jika salah satu gagal (misal server tidak terhubung), lanjutkan dengan data yang tersedia. Jangan error.

## Langkah 2 — Tampilkan Ringkasan

Format respons sebagai berikut:

```
Server    : [hostname/alias] ([server_type], mode: [mode])
Uptime    : [uptime]
Disk      : [usage]%
Memory    : [used]/[total]
Services  : [daftar service aktif, misal: nginx ✓, php-fpm ✓, mysql ✓]

Memory    : [jumlah] entries aktif ([jumlah] instruksi)
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
