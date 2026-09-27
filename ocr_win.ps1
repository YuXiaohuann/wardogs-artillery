param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]

$mAsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

# 1) StorageFile
$op = [Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $Path).ProviderPath)
$t = $mAsTask.MakeGenericMethod([Windows.Storage.StorageFile]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
$file = $t.Result
# 2) stream
$op = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
$t = $mAsTask.MakeGenericMethod([Windows.Storage.Streams.IRandomAccessStream]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
$stream = $t.Result
# 3) decoder -> software bitmap
$op = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
$t = $mAsTask.MakeGenericMethod([Windows.Graphics.Imaging.BitmapDecoder]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
$dec = $t.Result
$op = $dec.GetSoftwareBitmapAsync()
$t = $mAsTask.MakeGenericMethod([Windows.Graphics.Imaging.SoftwareBitmap]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
$bmp = $t.Result
if ($bmp.BitmapPixelFormat -ne 'Bgra8' -and $bmp.BitmapPixelFormat -ne 'Gray8') {
  $bmp = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bmp, [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8)
}
# 4) OCR
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage('en-US')
if (-not $eng) { $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $eng) { throw 'no OCR engine available' }
$op = $eng.RecognizeAsync($bmp)
$t = $mAsTask.MakeGenericMethod([Windows.Media.Ocr.OcrResult]).Invoke($null, @($op)); $t.Wait(-1) | Out-Null
$res = $t.Result
Write-Output $res.Text
