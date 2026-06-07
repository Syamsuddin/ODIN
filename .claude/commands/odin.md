Saat user menjalankan /odin, lakukan langkah berikut PERSIS dalam urutan ini:

## Langkah 1 — Tampilkan Banner

Tampilkan teks berikut PERSIS sebagai code block (jangan ubah karakter atau spasi apapun):

```

   ██████╗ ██████╗ ██╗███╗   ██╗
  ██╔═══██╗██╔══██╗██║████╗  ██║
  ██║   ██║██║  ██║██║██╔██╗ ██║
  ██║   ██║██║  ██║██║██║╚██╗██║
  ╚██████╔╝██████╔╝██║██║ ╚████║
   ╚═════╝ ╚═════╝ ╚═╝╚═╝  ╚═══╝

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚡ MCP Deploy Agent for Claude Code
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Created by Syams Ideris
  syamsuddin.ideris@gmail.com

```

## Langkah 2 — Load Konteks

Panggil 2 tool MCP secara paralel:
1. `mcp__odin__memory_digest` — untuk load semua memory (instruksi, profil server, profil user)
2. `mcp__odin__server_info` — untuk ambil status server terkini (disk, memory, uptime)

Jika salah satu gagal (misal server tidak terhubung), lanjutkan dengan data yang tersedia. Jangan error.

## Langkah 3 — Tampilkan Status

Setelah mendapat respons dari kedua tool, tampilkan ringkasan singkat dalam format ini:

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
```

## Langkah 4 — Siap Menerima Perintah

Akhiri dengan satu baris:

> Siap menerima perintah. Ketik apa saja tentang server Anda.

Jangan tambahkan penjelasan lain. Respons harus ringkas dan langsung.
