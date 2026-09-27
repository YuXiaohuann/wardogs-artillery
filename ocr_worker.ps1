$ErrorActionPreference='Stop'
# WARDOGS OCR resident worker: reads PNG path line by line from stdin, returns base64(UTF8 text) line by line on stdout.
# The WinRT OCR engine is created only once at startup; the process stays resident throughout, avoiding the overhead of launching a new powershell console window for every OCR call.
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]

$mAsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

function B64([string]$s) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }

$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage('en-US')
if (-not $eng) { $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $eng) {
  [Console]::Out.WriteLine('ERR ' + (B64 'no OCR engine available')); [Console]::Out.Flush(); exit 1
}
[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()

while ($true) {
  $line = [Console]::In.ReadLine()
  if ($null -eq $line -or $line -eq '__QUIT__') { break }
  $out = ''
  try {
    $op = [Windows.Storage.StorageFile]::GetFileFromPathAsync($line)
    $t = $mAsTask.MakeGenericMethod([Windows.Storage.StorageFile]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
    $file = $t.Result
    $op = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
    $t = $mAsTask.MakeGenericMethod([Windows.Storage.Streams.IRandomAccessStream]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
    $stream = $t.Result
    $op = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    $t = $mAsTask.MakeGenericMethod([Windows.Graphics.Imaging.BitmapDecoder]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
    $dec = $t.Result
    $op = $dec.GetSoftwareBitmapAsync()
    $t = $mAsTask.MakeGenericMethod([Windows.Graphics.Imaging.SoftwareBitmap]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
    $bmp = $t.Result
    if ($bmp.BitmapPixelFormat -ne 'Bgra8' -and $bmp.BitmapPixelFormat -ne 'Gray8') {
      $bmp = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bmp, [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8)
    }
    $op = $eng.RecognizeAsync($bmp)
    $t = $mAsTask.MakeGenericMethod([Windows.Media.Ocr.OcrResult]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
    $res = $t.Result
    $stream.Dispose()
    $out = B64 $res.Text
  } catch {
    $out = 'ERR ' + (B64 $_.Exception.Message)
  }
  [Console]::Out.WriteLine($out); [Console]::Out.Flush()
}
