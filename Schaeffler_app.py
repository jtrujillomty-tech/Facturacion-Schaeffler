import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Schaeffler FBAP Auto", page_icon="⚙️", layout="centered")

def asignar_fbap(datos_proveedor):
    datos_proveedor = str(datos_proveedor).upper()
    if "TRANSMISSION" in datos_proveedor or "WOOSTER" in datos_proveedor: return "WST"
    elif "SPECIAL MACHINERY" in datos_proveedor or "SMB" in datos_proveedor: return "SMB"
    elif "LIFETIME" in datos_proveedor or "VLS" in datos_proveedor: return "VLS"
    elif "AEROSPACE" in datos_proveedor or "AERO" in datos_proveedor: return "AERO"
    elif "SCHAEFFLER GROUP USA" in datos_proveedor: return "SG USA"
    return "REVISAR ENTIDAD"

def procesar_logica(df_it, df_routing, df_rate):
    # Llenamos vacíos
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
            
            if "LEO" in origen_bruto or "IRAPUATO" in origen_bruto: origen_busqueda = "IRAPUATO"
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
            
            if ref_val and ref_val != 'nan' and ped_val and ped_val != 'nan': reference_no = f"{ref_val} // {ped_val}"
            elif ped_val and ped_val != 'nan': reference_no = ped_val
            else: reference_no = ref_val if ref_val != 'nan' else ''
                
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
                'Container_No': str(row['Container_No']) if pd.notnull(row['Container_No']) else '',
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

# --- INTERFAZ DE USUARIO STREAMLIT ---
st.title("⚙️ Generador Automático FBAP - Schaeffler")
st.markdown("Sube el archivo crudo extraído por el equipo de TI. El sistema determinará automáticamente las rutas (Lane) y las tarifas (Rate).")

archivo_it = st.file_uploader("📥 Sube el Query de Facturación (Excel)", type=["xlsx"])

if archivo_it is not None:
    if st.button("🚀 Procesar Archivo"):
        with st.spinner("Cruzando datos con Routing Guide y Rate Card..."):
            try:
                # Leemos el archivo subido por el usuario
                df_it = pd.read_excel(archivo_it)
                
                # Leemos los archivos estáticos desde GitHub (deben estar en la misma carpeta del repo)
                # Asegúrate de que los nombres coincidan exactamente con cómo los subas a GitHub
                df_routing = pd.read_excel("2023-02-01 Routing Guide.xlsx", sheet_name=0, header=1)
                df_rate = pd.read_excel("2022-11-08 Rate Card.xlsx", sheet_name=0, header=1)
                
                # Procesamos
                df_resultado = procesar_logica(df_it, df_routing, df_rate)
                
                # Convertimos el resultado a un archivo en memoria para descargarlo
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_resultado.to_excel(writer, index=False)
                processed_data = output.getvalue()
                
                st.success("✅ ¡Reporte generado con éxito!")
                
                st.download_button(
                    label="💾 Descargar Reporte FBAP Final",
                    data=processed_data,
                    file_name="Reporte_Automatizado_FBAP.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
            except Exception as e:
                st.error(f"Ocurrió un error al procesar el archivo: {e}")