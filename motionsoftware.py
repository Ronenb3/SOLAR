import RPi.GPIO as GPIO
from time import sleep
import suncalc
from datetime import datetime
import math

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

def altitudeSteps(angle):
    ##Imperically Derived Formula
    steps = 2*((16956+(-101)*angle+(-1.26)*(angle**2)+(3.82*10**(-3))*(angle**3)))
    return round(steps)

def azimuthSteps(angle):
    ##400 Steps/Rev
    ##40:1 Gear Ratio
    R_2 = angle/360
    R_1 = R_2*40
    steps = R_1*1000
    return round(steps)
                 
def move (step_count, motor):
        # 0 if azimuth, 1 altitude
    azEn = 16
    azDir = 20
    azPul = 21
    
    if motor == 1: ## Altitude
        azEn = 26
        azDir = 19
        azPul = 13
    
    GPIO.setup(azEn,GPIO.OUT) # enable high enable low disable
    GPIO.setup(azDir,GPIO.OUT) # direction
    GPIO.setup(azPul,GPIO.OUT) # step
    
    if step_count < 0:
        GPIO.output(azDir, 0)
        ## Set motor to counter clockwise
        
    if step_count > 0:
        GPIO.output(azDir, 1)
        ## Set motor to counter clockwise
    
    delay = 0.001

    for x in range(abs(step_count)):
        GPIO.output(azPul, GPIO.HIGH)
        sleep(delay)
        GPIO.output(azPul, GPIO.LOW)
        sleep(delay)

lat, lon = 42.25, -71.82

## Set Start Position
azHome = 90
altHome = 91

azPrev = azHome
altPrev = altHome

while True:
        ##Get Next Position
    pos = suncalc.get_position(datetime.now(),lon,lat)
    azNext = round((math.degrees(pos['azimuth'])+180)*(1000/9))*(9/1000) ##Rounded to nearest step
    altNext = math.degrees(pos['altitude'])
    
    print(datetime.now())
    print("Azimuth = " + str(azNext))
    print("Altitude = " + str(altNext))
    
    ##Check for sunset
    if(altNext < 1):
        break
        
        ##Find Change
    azDelta = azNext - azPrev
    azSteps = azimuthSteps(azDelta)
        ##Find change from prev position to next
    altSteps = altitudeSteps(altNext) - altitudeSteps(altPrev)

    print("Azimuth Delta = " + str(azDelta))

        ##Move Commands
    move(azSteps,0)
    move(altSteps,1)
    
        ##Reset current position
    azPrev = azNext
    altPrev = altNext
        ##Wait n seconds
    sleep(300)
    
##Go Home
azDelta = azHome - azPrev
azSteps = azimuthSteps(azDelta)
altSteps = altitudeSteps(altHome) - altitudeSteps(altPrev)

move(azSteps,0)
move(altSteps,1)

GPIO.cleanup()