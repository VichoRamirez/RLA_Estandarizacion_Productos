@echo off
cd /d %~dp0
python -m pip install -r requirements.txt
if not exist db\rla_products.db python run_pipeline.py
python -m streamlit run app\app.py
pause
