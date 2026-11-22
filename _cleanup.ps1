$ErrorActionPreference = 'Stop'
$root = 'C:\Users\ThankGod Tec\Desktop\tutr\ai-classroom'
$templates = Join-Path $root 'app\lib\blackboard\templates'
if (Test-Path $templates) { Remove-Item -LiteralPath $templates -Recurse -Force; Write-Output "templates_deleted" } else { Write-Output "templates_missing" }
$reg = Join-Path $root 'app\lib\blackboard\registry\drawing_registry.dart'
if (Test-Path $reg) { Remove-Item -LiteralPath $reg -Force; Write-Output "registry_deleted" } else { Write-Output "registry_missing" }
$pt = Join-Path $root 'app\lib\blackboard\tables\periodic_table_mini_template.dart'
if (Test-Path $pt) { Remove-Item -LiteralPath $pt -Force; Write-Output "periodic_deleted" } else { Write-Output "periodic_missing" }