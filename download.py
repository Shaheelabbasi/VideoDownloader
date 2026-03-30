import yt_dlp

url = "https://youtube.com/shorts/WL5pHorDqyE?si=nB7B9FtoVmwLd8Ou"

ydl_opts = {
    'outtmpl': '%(title)s.%(ext)s',
    'format': 'best'
}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print("Download completed!")
except Exception as e:
    print("Error:", e)