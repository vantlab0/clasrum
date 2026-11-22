Select-String -Path backend\app\api\lessons.py -Pattern 'flashcard|quiz|video|router' | ForEach-Object { $_.LineNumber.ToString() + ': ' + $_.Line }
