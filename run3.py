import sys
import os
import time
import traceback
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.animation as animation
import RPi.GPIO as GPIO
import pigpio
import glob
import threading
from Vedirect import Vedirect

port1 = '/dev/ttyUSB0'
ve1 = Vedirect(port1, 60)

port2 = '/dev/ttyUSB1'
ve2 = Vedirect(port2, 60)

fig, axs = plt.subplots(3, 1, figsize=(8, 10))
plt.tight_layout()

# initialize data
xs1 = []
BatteryVoltage = []
BatteryCurrent = []
BatteryEnergy = []
PanelVoltage = []
PanelPower = []
DoD1_series = []

xs2 = []
BatVolt2 = []
BatCur2 = []
BatEn2 = []
PanelVolt2 = []
PanelPow2 = []
DoD2_series = []

startT = time.time()
fileRoot = "BatteryDat"

#Creates file with data
def create_new_file():
    fileList = glob.glob(fileRoot + "*")
    testList = []
    for m in fileList:
        strn = m.split("_")[-1]
        strn = strn.split(".")[0]
        testList.append(int(strn))
    if len(fileList) < 1:
        filenm = fileRoot + "_" + "0.txt"
    else:
        numstr = str(max(testList) + 1)
        filenm = fileRoot + "_" + numstr + ".txt"
    print("Data will be saved to File named", filenm)
    return filenm

filenm = create_new_file()
with open(filenm, "w+") as f:
    f.write("BatV1, BatI1, BatEn1, PanelV1, PanelP1, DoD1, BatV2, BatI2, BatEn2, PanelV2, PanelP2, DoD2, Hours1, Hours2\n")

# Battery & DoD Data
CAPACITY_AH = 40.0
R_INTERNAL = 0.015 

#DoD Manufacturer Data Points From User Manuel Graph
dod_axis = np.array([0,10,20,30,40,50,60,70,80,90,100])
curve_C2  = np.array([13.6,13.4,13.3,13.2,13.18,13.1,13.0,12.9,12.8,12.6,12.0])
curve_C3  = np.array([13.6,13.45,13.35,13.28,13.23,13.15,13.05,12.95,12.83,12.65,12.10])
curve_C5  = np.array([13.6,13.5,13.42,13.35,13.28,13.20,13.12,13.00,12.90,12.70,12.20])
curve_C10 = np.array([13.6,13.55,13.48,13.42,13.35,13.27,13.18,13.05,12.92,12.75,12.30])

def interp_dod_from_curve(V, curve):
    return float(np.interp(V, curve[::-1], dod_axis[::-1]))

def dod_from_voltage_current(V_meas, I_meas):
    V_oc = V_meas + I_meas * R_INTERNAL
    C_rate = abs(I_meas) / CAPACITY_AH

    dod10 = interp_dod_from_curve(V_oc, curve_C10)
    dod5  = interp_dod_from_curve(V_oc, curve_C5)
    dod3  = interp_dod_from_curve(V_oc, curve_C3)
    dod2  = interp_dod_from_curve(V_oc, curve_C2)

    if C_rate <= 0.10:
        return dod10
    elif C_rate <= 0.20:
        weight = (C_rate - 0.10) / 0.10
        return (1 - weight) * dod10 + weight * dod5
    elif C_rate <= 0.33:
        weight = (C_rate - 0.20) / 0.13
        return (1 - weight) * dod5 + weight * dod3
    elif C_rate <= 0.50:
        weight = (C_rate - 0.33) / 0.17
        return (1 - weight) * dod3 + weight * dod2
    else:
        return dod2

# Device Streaming 
def stream_device1():
    while True:
        try:
            packet = ve1.read_data_single()
            V = float(packet['V']) / 1000
            I = float(packet['I']) / 1000
            BE = V * I / 1000
            VPV = float(packet['VPV']) / 1000
            PPV = float(packet['PPV'])
            DoD1 = dod_from_voltage_current(V, I)

            print(f"[{time.strftime('%H:%M:%S')}] Device1: V={V:.2f}V, I={I:.2f}A, VPV={VPV:.2f}V, PPV={PPV:.1f}W, DoD={DoD1:.1f}%", flush=True)

            BatteryVoltage.append(V)
            BatteryCurrent.append(I)
            BatteryEnergy.append(BE)
            PanelVoltage.append(VPV)
            PanelPower.append(PPV)
            DoD1_series.append(DoD1)
            now = time.time()
            xs1.append((now - startT) / 3600)

            with open(filenm, "a") as f:
                print("%.2f,%.2f,%.2f,%.2f,%.2f,%.1f,,,,,%.4f" %
                      (V, I, BE, VPV, PPV, DoD1, xs1[-1]), file=f, flush=True)
        except Exception as e:
            print("Error in stream_device1:", e, flush=True)
            traceback.print_exc()

def stream_device2():
    while True:
        try:
            packet = ve2.read_data_single()
            V2 = float(packet['V']) / 1000
            I2 = float(packet['I']) / 1000
            BE2 = V2 * I2 / 1000
            DoD2 = dod_from_voltage_current(V2, I2)

            print(f"[{time.strftime('%H:%M:%S')}] Device2: V={V2:.2f}V, I={I2:.2f}A, DoD={DoD2:.1f}%", flush=True)

            BatVolt2.append(V2)
            BatCur2.append(I2)
            BatEn2.append(BE2)
            DoD2_series.append(DoD2)
            now = time.time()
            xs2.append((now - startT) / 3600)

            with open(filenm, "a") as f:
                print(",,,,%.2f,%.2f,%.2f,%.4f" %
                      (DoD2, V2, I2, xs2[-1]), file=f, flush=True)
        except Exception as e:
            print("Error in stream_device2:", e, flush=True)
            traceback.print_exc()

# Start Threads 
threading.Thread(target=stream_device1, daemon=True).start()
threading.Thread(target=stream_device2, daemon=True).start()

# Plotting graphs 
def animate(wTime):
    

    # Tracking Panel
    axs[0].clear()
    if len(xs1):
        axs[0].plot(xs1, PanelVoltage, "-k", label="Panel Voltage 1")
        axs[0].plot(xs1, PanelPower, "c", label="Panel Power 1")
    axs[0].set_title('Tracking Panel')
    axs[0].set(ylabel='Volts / Watts', xlabel='Time (hours)')
    if axs[0].lines: axs[0].legend(loc="upper left")

    # DoD vs Time 
    axs[1].clear()
    if len(xs1) > 0 and len(xs1) == len(DoD1_series):
        axs[1].plot(xs1, DoD1_series, "m", label="Device1")
    if len(xs2) > 0 and len(xs2) == len(DoD2_series):
        axs[1].plot(xs2, DoD2_series, "g", label="Device2")
    axs[1].set_title("Depth of Discharge vs Time")
    axs[1].set(ylabel="DoD (%)", xlabel="Time (hours)")
    if axs[1].get_lines(): axs[1].legend(loc="upper left")

    # Voltage vs DoD
    axs[2].clear()
    if len(DoD1_series) > 0 and len(DoD1_series) == len(BatteryVoltage):
        axs[2].plot(DoD1_series, BatteryVoltage, "m.-", label="Device1")
    if len(DoD2_series) > 0 and len(DoD2_series) == len(BatVolt2):
        axs[2].plot(DoD2_series, BatVolt2, "g.-", label="Device2")
    axs[2].plot(dod_axis, curve_C10, "k--", label="C/10 Curve")
    axs[2].plot(dod_axis, curve_C5, "b--", label="C/5 Curve")
    axs[2].plot(dod_axis, curve_C3, "r--", label="C/3 Curve")
    axs[2].plot(dod_axis, curve_C2, "c--", label="C/2 Curve")
    axs[2].set_title("Battery Voltage vs Depth of Discharge")
    axs[2].set(xlabel="DoD (%)", ylabel="Voltage (V)")
    if axs[2].get_lines(): axs[2].legend(loc="upper right")

ani = animation.FuncAnimation(fig, animate, interval=5000, cache_frame_data=False)
plt.show()