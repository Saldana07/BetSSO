# Registra (o re-registra) la tarea programada que ejecuta el flujo diario.
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File register_task.ps1
#
# - Corre a las 08:00 y tambien 2 minutos despues de iniciar sesion (si el PC
#   estaba apagado a las 08:00). daily.bat se salta solo si ya corrio hoy.
# - Sin comillas en el comando: cmd /c recorta la primera y la ultima comilla y
#   rompe la ruta. Las rutas de este proyecto no tienen espacios.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$name = "Apuesta Segura - flujo diario"

$action   = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c $root\daily.bat auto >> $root\backend\daily.log 2>&1" `
    -WorkingDirectory $root
$daily    = New-ScheduledTaskTrigger -Daily -At 08:00
$logon    = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logon.Delay = "PT2M"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $name -Action $action -Trigger @($daily, $logon) -Settings $settings `
    -Description "Liquida picks pendientes y genera los de hoy. Corre a las 08:00 y 2 min despues de iniciar sesion; si ya corrio hoy, se salta solo." `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskName $name
Write-Output ("Tarea '{0}' registrada. Estado: {1}" -f $name, $t.State)
Write-Output ("Accion: cmd.exe " + $t.Actions[0].Arguments)
