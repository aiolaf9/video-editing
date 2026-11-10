# Sampel video ucapan untuk uji (CI Windows): suara Windows SAPI (offline, bawaan OS) + gambar uji FFmpeg.
param([string]$Text, [string]$Ffmpeg, [string]$Out)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$wav = [System.IO.Path]::ChangeExtension($Out, ".wav")
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
Write-Host ("Suara: " + (($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }) -join ", "))
$synth.Rate = 0
$synth.SetOutputToWaveFile($wav)
$synth.Speak((Get-Content -Raw $Text))
$synth.Dispose()
& $Ffmpeg -v error -y -f lavfi -i "testsrc2=size=1280x720:rate=25" -i $wav -map 0:v -map 1:a -c:v libx264 `
  -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 128k -shortest $Out
if ($LASTEXITCODE -ne 0) { throw "ffmpeg gagal membuat sampel" }
Get-Item $Out | Select-Object Name, Length
