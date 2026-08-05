import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO
from google import genai
import tempfile
import os

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor FBAP - Schaeffler", page_icon="⚙️", layout="wide")

# Inicializar cliente de Gemini usando los Secrets seguros de Streamlit Cloud
# (Asegúrate de configurar GOOGLE_API_KEY en el panel de Secrets de tu app en Streamlit)
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    client = genai.Client(api_key=API_KEY)
except Exception:
    client = None

def asignar_fbap(consignee):
    consignee = str(consignee).upper()
    if "TRANSMISSION" in consignee or "WOOSTER" in consignee: return "WST"
    elif "SPECIAL MACHINERY" in consignee or "SMB" in consignee: return "SMB"
    elif "LIFETIME" in consignee or "VLS" in consignee: return "VLS"
    elif "AEROSPACE" in consignee or "AERO" in consignee: return "AERO"
    elif "SCHAEFFLER GROUP USA" in consignee: return "SG USA"
    return "REVISAR ENTIDAD"

# ==========================================
# MOTOR 1: PROCESAMIENTO DESDE EXCEL (IT)
# ==========================================
def procesar_logica_it(df_it, df_routing, df_rate):
    df_it['Invoice_No'] = df_it['Invoice_No'].fillna('PENDIENTE')
    
    agg_funcs = {
        'Reference_No': 'first', 'PEDIMENTO': 'first', 'Impo_Expo': 'first',
        'Container_No': 'first', 'Tipo_envio': 'first', 'Tipo_Caja': 'first',             
        'Proveedor': 'first', 'DIRECCION_FACTURA': 'first', 'DIRECCION_FPEDIMENTO': 'first',
        'Bill_to_party_Mexico': 'first', 'Invoice_Creation_Date': 'first',
        'TOTAL_Americana': 'first', 'TOTAL_US CUSTOM BROKER': 'first'
    }
    
    entradas_unicas = df_it.groupby(['Invoice_No', 'EntryNumber'], as_index=False).agg(agg_funcs)
    filas_reporte = []
    facturas = entradas_unicas['Invoice_No'].unique()
    
    for factura in facturas:
        df_factura = entradas_unicas[entradas_unicas['Invoice_No'] == factura]
        es_facturado = (factura != 'PENDIENTE')
        cantidad_entries = len(df_factura)
        
        if es_facturado:
            cobro_broker = float(df_factura.iloc[0]['TOTAL_US CUSTOM BROKER']) if pd.notnull(df_factura.iloc[0]['TOTAL_US CUSTOM BROKER']) else 0.0
            total_factura = float(df_factura.iloc[0]['TOTAL_Americana']) if pd.notnull(df_factura.iloc[0]['TOTAL_Americana']) else 0.0
            esperado = cantidad_entries * 63.00
            cuadra = abs(cobro_broker - esperado) < 0.01 and cobro_broker > 0
        else:
            cobro_broker = total_factura = esperado = 0.0
            cuadra = False
        
        filas_factura_actual = []
        for i, row in df_factura.iterrows():
            origen_bruto = str(row['DIRECCION_FPEDIMENTO']).upper()
            destino_bruto = str(row['DIRECCION_FACTURA']).upper()
            
            if origen_bruto == 'NAN' or origen_bruto == 'NONE' or origen_bruto.strip() == '':
                origen_busqueda = "IRAPUATO"
            elif "LEO" in origen_bruto or "IRAPUATO" in origen_bruto: origen_busqueda = "IRAPUATO"
            else: origen_busqueda = origen_bruto.split(',')[0].strip()
            
            if "FORT MILL" in destino_bruto: destino_busqueda = "FORT MILL"
            elif "WOOSTER" in destino_bruto: destino_busqueda = "WOOSTER"
            elif "CHERAW" in destino_bruto: destino_busqueda = "CHERAW"
            else: destino_busqueda = destino_bruto.split(',')[0].strip()
                
            filtro_lane = df_routing[
                df_routing['Shipper City ZipCode Country'].str.contains(origen_busqueda, case=False, na=False) & 
                df_routing['Receiver City ZipCode Country'].str.contains(destino_busqueda, case=False, na=False)
            ]
            
            if not filtro_lane.empty:
                lane_id = filtro_lane.iloc[0]['Lane ID']
                delivery_location = str(filtro_lane.iloc[0].get('Receiver Location', destino_busqueda))
            else:
                lane_id = f"No existe Lane: {origen_busqueda} - {destino_busqueda}"
                delivery_location = destino_busqueda
            
            operacion_bruta = str(row['Impo_Expo']).upper()
            operacion_reporte = "IMP" if "EXP" in operacion_bruta else "EXP"
            operacion_buscar = "US import" if operacion_reporte == "IMP" else "MX export"
            tipo_envio = "direct" if "DIRECT" in str(row['Tipo_envio']).upper() else "consol"
            
            tipo_caja = str(row['Tipo_Caja']).upper()
            if '53' in tipo_caja: equipo_rate = "Dry Van 53'"
            elif '3.5' in tipo_caja: equipo_rate = "Dry Van 3.5 t"
            else: equipo_rate = "Dry Van 53'" 
                
            filtro_rate = df_rate[
                df_rate['Export / Import'].str.contains(operacion_buscar, case=False, na=False) &
                df_rate['Type'].str.contains(tipo_envio, case=False, na=False) &
                (df_rate['Equipment'] == equipo_rate) 
            ]
            
            rate_id = filtro_rate.iloc[0]['Rate ID'] if not filtro_rate.empty else f"No existe rate para: {operacion_buscar} / {tipo_envio} / {equipo_rate}"
            rate_card_id = f"{lane_id} // {rate_id}"
            
            ref_val = str(row['Reference_No']).strip()
            ped_val = str(row['PEDIMENTO']).strip().replace(' ', '')
            if ped_val.endswith('.0'): ped_val = ped_val[:-2]
            
            if ref_val and ref_val != 'NAN' and ped_val and ped_val != 'NAN': reference_no = f"{ref_val} // {ped_val}"
            elif ped_val and ped_val != 'NAN': reference_no = ped_val
            else: reference_no = ref_val if ref_val != 'NAN' else ''
                
            if es_facturado:
                monto_asignado = 63.00 if cuadra else total_factura
                fecha_creacion = row['Invoice_Creation_Date']
                if pd.notnull(fecha_creacion):
                    fecha_creacion = pd.to_datetime(fecha_creacion).strftime('%m/%d/%Y')
                else: fecha_creacion = ''
            else:
                monto_asignado = ''
                fecha_creacion = ''
            
            proveedor_limpio = str(row['Proveedor']).upper()
            if " PLANT" in proveedor_limpio: proveedor_limpio = re.split(r'\s+PLANT', proveedor_limpio)[0].strip()
            proveedor_limpio = proveedor_limpio.replace('SCHAEFFLER TRANSMISSION SYSTEM LL', 'SCHAEFFLER TRANSMISSION SYSTEM LLC')
            if proveedor_limpio.endswith(" LL"): proveedor_limpio += "C"

            datos_proveedor = str(row['Proveedor']) + " " + str(row['DIRECCION_FACTURA'])
            
            fila = {
                'FBAP_Tab': asignar_fbap(datos_proveedor),
                'Numero de Entry': str(row['EntryNumber']).replace('-', ''),
                'Reference_No': reference_no,
                'Container_No': str(row['Container_No']) if pd.notnull(row['Container_No']) and str(row['Container_No']) != 'nan' else '',
                'Contracted Rate Card ID': rate_card_id,
                'Month_of_Delivery': "March", 
                'Schaeffler_Delivery_Location_USA': delivery_location, 
                'Transport_Medium': 'Ground',
                'Import_Export_Domestic': operacion_reporte,
                'Bill_to_party_Mexico': proveedor_limpio,
                'Invoice_No': factura if es_facturado else '',
                'Invoice_Creation_Date': fecha_creacion,
                'X': '', 'X2': '', 'X3': '',
                'Invoice_Amount_Subtotal_MXN': monto_asignado
            }
            filas_factura_actual.append(fila)
            
        if es_facturado and cuadra:
            restante = total_factura - cobro_broker
            if restante > 0.01 and len(filas_factura_actual) > 0:
                fila_extra = filas_factura_actual[0].copy()
                fila_extra['Invoice_Amount_Subtotal_MXN'] = round(restante, 2)
                filas_factura_actual.append(fila_extra)
                
        filas_reporte.extend(filas_factura_actual)
        
    return pd.DataFrame(filas_reporte)

# ==========================================
# MOTOR 2: PROCESAMIENTO DESDE PDF CON GEMINI
# ==========================================
def extraer_datos_con_gemini(archivo_pdf_bytes, nombre_archivo):
    # 1. Guardamos el archivo temporalmente en el servidor de Streamlit para que Gemini detecte el .pdf
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(archivo_pdf_bytes)
        ruta_temporal = tmp.name
        
    try:
        # 2. Subimos el archivo usando la ruta temporal
        archivo_subido = client.files.upload(file=ruta_temporal)
        
        prompt = """
        Actúa como un extractor de datos aduanales. Analiza TODO el documento (incluso si no tiene pedimento o factura de aduana formal, busca los Inward Cargo Manifest) y devuelve ÚNICAMENTE un objeto JSON válido con esta estructura exacta, sin texto adicional:
        {
          "invoice_no": "",
          "invoice_date": "",
          "total_factura": 0.00,
          "us_custom_broker": 0.00,
          "invoice_comment": "",
          "referencia": "",
          "pedimento": "",
          "entries": [
            {
              "numero_entry": "NFP12345678",
              "consignee": "Nombre exacto del consignatario o dueño en el manifiesto",
              "container": "Contenedor o casilla 8",
              "referencia": "Referencia si la hay",
              "pedimento": "Pedimento si lo hay",
              "ciudad_origen": "Ciudad de origen del shipper",
              "ciudad_destino": "Ciudad de destino",
              "medio_transporte": "Ground",
              "operacion": "IMP/EXP"
            }
          ]
        }
        ⚠️ INSTRUCCIONES:
        1. Extrae TODOS los números de Entry (que empiezan con NFP) de los manifiestos de carga.
        2. Si no hay factura ni montos, deja los campos numéricos en 0 y textos vacíos "".
        3. Asegúrate de extraer la ciudad origen y destino lo más exactas posibles.
        """
        
        max_reintentos = 3
        for intento in range(max_reintentos):
            try:
                respuesta = client.models.generate_content(
                    model='gemini-3.5-flash-lite',
                    contents=[archivo_subido, prompt]
                )
                break # Si tiene éxito, rompe el ciclo y continúa
            except Exception as e:
                error_str = str(e)
                if "503" in error_str and intento < max_reintentos - 1:
                    # Si es error 503 y aún nos quedan intentos, esperamos 5 segundos
                    import time
                    time.sleep(5)
                else:
                    # Si no es 503, o ya nos gastamos los 3 intentos, lanza el error
                    raise e
        
        texto_json = respuesta.text.replace('```json', '').replace('```', '').strip()
        datos = json.loads(texto_json)
        
        # 3. Limpieza: Borramos de Gemini y del servidor local
        try:
            client.files.delete(name=archivo_subido.name)
            os.remove(ruta_temporal)
        except Exception:
            pass
            
        return datos
        
    except Exception as e:
        # Limpieza en caso de error
        try:
            if 'archivo_subido' in locals():
                client.files.delete(name=archivo_subido.name)
            os.remove(ruta_temporal)
        except Exception:
            pass
        raise e # Lanza el error para que Streamlit lo muestre y sepamos qué falló
def procesar_logica_pdf(datos_json, df_routing, df_rate):
    filas_reporte = []
    cobro_broker = float(datos_json.get('us_custom_broker', 0))
    total_factura = float(datos_json.get('total_factura', 0))
    cantidad_entries = len(datos_json.get('entries', []))
    
    esperado = cantidad_entries * 63.00
    cuadra = abs(cobro_broker - esperado) < 0.01 and cobro_broker > 0
    comentario_factura = datos_json.get('invoice_comment', '')
    
    for entry in datos_json.get('entries', []):
        origen_bruto = str(entry.get('ciudad_origen', '')).upper()
        destino_bruto = str(entry.get('ciudad_destino', '')).upper()
        
        if not origen_bruto or origen_bruto == 'NAN': origen_busqueda = "IRAPUATO"
        elif "LEO" in origen_bruto or "IRAPUATO" in origen_bruto: origen_busqueda = "IRAPUATO"
        else: origen_busqueda = origen_bruto.split(',')[0].strip()
        
        if "FORT MILL" in destino_bruto: destino_busqueda = "FORT MILL"
        elif "WOOSTER" in destino_bruto: destino_busqueda = "WOOSTER"
        elif "CHERAW" in destino_bruto: destino_busqueda = "CHERAW"
        else: destino_busqueda = destino_bruto.split(',')[0].strip()
            
        filtro_lane = df_routing[
            df_routing['Shipper City ZipCode Country'].str.contains(origen_busqueda, case=False, na=False) & 
            df_routing['Receiver City ZipCode Country'].str.contains(destino_busqueda, case=False, na=False)
        ]
        
        if not filtro_lane.empty:
            lane_id = filtro_lane.iloc[0]['Lane ID']
            delivery_location = str(filtro_lane.iloc[0].get('Receiver Location', destino_busqueda))
        else:
            lane_id = f"No existe Lane: {origen_busqueda} - {destino_busqueda}"
            delivery_location = destino_busqueda

        operacion_bruta = str(entry.get('operacion', 'IMP')).upper()
        operacion_reporte = "IMP" if "EXP" in operacion_bruta else "EXP"
        operacion_buscar = "US import" if operacion_reporte == "IMP" else "MX export"
        
        filtro_rate = df_rate[
            df_rate['Export / Import'].str.contains(operacion_buscar, case=False, na=False) &
            df_rate['Type'].str.contains("direct", case=False, na=False)
        ]
        rate_id = filtro_rate.iloc[0]['Rate ID'] if not filtro_rate.empty else "No existe rate"
        rate_card_id = f"{lane_id} // {rate_id}"
        
        ref_val = str(entry.get('referencia') or datos_json.get('referencia', '')).strip()
        ped_val = str(entry.get('pedimento') or datos_json.get('pedimento', '')).strip().replace(' ', '')
        if ped_val.endswith('.0'): ped_val = ped_val[:-2]
        
        if ref_val and ref_val != 'NAN' and ped_val and ped_val != 'NAN': reference_no = f"{ref_val} // {ped_val}"
        elif ped_val and ped_val != 'NAN': reference_no = ped_val
        else: reference_no = ref_val if ref_val != 'NAN' else ''

        monto_asignado = 63.00 if cuadra else (total_factura if total_factura > 0 else '')
        consignee_val = entry.get('consignee', '')
        
        fila = {
            'FBAP_Tab': asignar_fbap(consignee_val),
            'Numero de Entry': str(entry.get('numero_entry', '')).replace('-', ''),
            'Reference_No': reference_no,
            'Container_No': entry.get('container', ''),
            'Contracted Rate Card ID': rate_card_id,
            'Month_of_Delivery': "March", 
            'Schaeffler_Delivery_Location_USA': delivery_location, 
            'Transport_Medium': 'Ground',
            'Import_Export_Domestic': operacion_reporte,
            'Bill_to_party_Mexico': str(consignee_val).upper(),
            'Invoice_No': datos_json.get('invoice_no', ''),
            'Invoice_Creation_Date': datos_json.get('invoice_date', ''),
            'X': '', 'X2': '', 'X3': '',
            'Invoice_Amount_Subtotal_MXN': monto_asignado
        }
        filas_reporte.append(fila)
        
    return pd.DataFrame(filas_reporte)

# ==========================================
# INTERFAZ WEB STREAMLIT
# ==========================================
st.title("⚙️ Generador Automático FBAP - Schaeffler")

# Creamos dos pestañas para separar ambos métodos
pestana_it, pestana_pdf = st.tabs(["📊 Opción 1: Reporte Excel de IT (Post/Pre-Factura)", "📄 Opción 2: Lector de PDF con IA (Sin Pedimento)"])

# CARGA DE BASES ESTÁTICAS DE RESPALDO
try:
    df_routing = pd.read_excel("2023-02-01 Routing Guide.xlsx", sheet_name=0, header=1)
    df_rate = pd.read_excel("2022-11-08 Rate Card.xlsx", sheet_name=0, header=1)
except Exception as e:
    st.error(f"⚠️ Faltan los archivos estáticos en el repositorio de GitHub (Routing Guide o Rate Card): {e}")
    st.stop()

with pestana_it:
    st.subheader("Cruce masivo mediante el Query de IT")
    archivo_it = st.file_uploader("📥 Sube el Excel de Facturación de IT", type=["xlsx"], key="it_file")
    
    if archivo_it is not None:
        if st.button("🚀 Procesar Excel de IT"):
            with st.spinner("Procesando y cruzando rutas..."):
                try:
                    df_it = pd.read_excel(archivo_it)
                    df_resultado = procesar_logica_it(df_it, df_routing, df_rate)
                    
                    output = BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        df_resultado.to_excel(writer, index=False)
                    
                    st.success("✅ ¡Reporte generado con éxito!")
                    st.download_button("💾 Descargar Excel FBAP Final", output.getvalue(), "Reporte_FBAP_IT.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                except Exception as e:
                    st.error(f"Error procesando el Excel: {e}")

with pestana_pdf:
    st.subheader("Extracción inteligente mediante PDF (Ideal para expedientes sin pedimento)")
    archivo_pdf = st.file_uploader("📥 Sube el PDF (Manifiesto / Entry)", type=["pdf"], key="pdf_file")
    
    if archivo_pdf is not None:
        if st.button("🧠 Extraer con IA y Generar Reporte"):
            if client is None:
                st.error("⚠️ La API Key de Google Gemini no está configurada en los Secrets de Streamlit.")
            else:
                with st.spinner("Analizando documento con Inteligencia Artificial..."):
                    try:
                        bytes_pdf = archivo_pdf.read()
                        datos_json = extraer_datos_con_gemini(bytes_pdf, archivo_pdf.name)
                        
                        if datos_json and len(datos_json.get('entries', [])) > 0:
                            df_resultado = procesar_logica_pdf(datos_json, df_routing, df_rate)
                            
                            output = BytesIO()
                            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                                df_resultado.to_excel(writer, index=False)
                            
                            st.success("✅ ¡Datos extraídos y cruzados con éxito!")
                            st.download_button("💾 Descargar Excel FBAP de PDF", output.getvalue(), "Reporte_FBAP_PDF.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                        else:
                            st.warning("⚠️ La IA no pudo detectar entries válidos en el PDF. Revisa el documento.")
                    except Exception as e:
                        st.error(f"Error procesando el PDF con IA: {e}")
