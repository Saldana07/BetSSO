"""Compatibilidad: el trabajo de este script (traer por ID el resultado de los
partidos con picks pendientes) ahora lo hace jobs/fetch_match_data.py, que en
la MISMA petición trae también las estadísticas y reparte la cuota de API
entre picks, mercados evaluados y el complemento del histórico.

Uso:
    python -m jobs.sync_pending_results   (equivale a python -m jobs.fetch_match_data)
"""
from jobs.fetch_match_data import main

if __name__ == "__main__":
    main()
