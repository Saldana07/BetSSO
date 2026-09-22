# Registra (o re-registra) las dos tareas programadas del sistema.
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File register_task.ps1
#
# 1) "Apuesta Segura - flujo diario": partidos y cuotas de hoy y de manana,
#    liquidacion y picks. Corre a las 08:00 y 2 minutos despues de iniciar
#    sesion (si el PC estaba apagado a las 08:00). daily.bat se salta solo si
#    ya corrio hoy y tiene candado contra ejecuciones en paralelo.
# 2) "Apuesta Segura - liquidar": cada 2 horas trae los resultados y las
#    estadisticas de los partidos con picks pendientes y los liquida, para
#    que "Hoy" y "Ayer" muestren acerto/fallo sin esperar al dia siguiente.
#
# - Ambas corren OCULTAS a traves de run_hidden.vbs (wscript): sin el, el
#   Programador abre una consola visible en cada corrida.
# - Sin comillas en los argumentos: las rutas de este proyecto no tienen espacios.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

# --- 1) Flujo diario ---
$name = "Apuesta Segura - flujo diario"
# Via run_hidden.vbs: sin ventana de consola (el Programador abria una visible
# en cada corrida aunque la app no se estuviera usando).
$action = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "//B //Nologo $root\run_hidden.vbs $root\daily.bat auto $root\backend\daily.log" `
    -WorkingDirectory $root
$daily = New-ScheduledTaskTrigger -Daily -At 08:00
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logon.Delay = "PT2M"
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($daily, $logon) -Settings $settings `
    -Description "Partidos, cuotas y picks de hoy y manana; liquida lo pendiente. 08:00 y 2 min tras iniciar sesion; si ya corrio hoy, se salta." `
    -Force | Out-Null
$t = Get-ScheduledTask -TaskName $name
Write-Output ("Tarea '{0}' registrada. Estado: {1}" -f $name, $t.State)

# --- 2) Liquidacion cada 2 horas ---
$name2 = "Apuesta Segura - liquidar"
$action2 = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "//B //Nologo $root\run_hidden.vbs $root\settle.bat auto $root\backend\settle.log" `
    -WorkingDirectory $root
# Repeticion cada 2 h durante 10 anos (el Programador no acepta "indefinido"), empezando dentro de 5 minutos.
$every2h = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName $name2 -Action $action2 -Trigger $every2h -Settings $settings `
    -Description "Cada 2 h: trae resultados y estadisticas de los partidos con picks pendientes y los liquida." `
    -Force | Out-Null
$t2 = Get-ScheduledTask -TaskName $name2
Write-Output ("Tarea '{0}' registrada. Estado: {1}" -f $name2, $t2.State)
