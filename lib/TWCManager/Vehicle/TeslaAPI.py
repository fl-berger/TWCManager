class CarApi:

  import json
  import re
  import time

  carApiLastErrorTime = 0
  carApiBearerToken   = ''
  carApiRefreshToken  = ''
  carApiTokenExpireTime = time.time()
  carApiLastStartOrStopChargeTime = 0
  carApiVehicles      = []
  config              = None
  debugLevel          = 0
  master              = None

  # Transient errors are ones that usually disappear if we retry the car API
  # command a minute or less later.
  # 'vehicle unavailable:' sounds like it implies the car is out of connection
  # range, but I once saw it returned by drive_state after wake_up returned
  # 'online'. In that case, the car is reacahble, but drive_state failed for some
  # reason. Thus we consider it a transient error.
  # Error strings below need only match the start of an error response such as:
  # {'response': None, 'error_description': '',
  # 'error': 'operation_timedout for txid `4853e3ad74de12733f8cc957c9f60040`}'}
  carApiTransientErrors = ['upstream internal error', 
                           'operation_timedout',
                           'vehicle unavailable']

  # Define minutes between retrying non-transient errors.
  carApiErrorRetryMins = 10

  def __init__(self, config):
    self.config = config
    self.debugLevel = config['config']['debugLevel']

  def addVehicle(self, json):
    self.carApiVehicles.append(CarApiVehicle(json, self, self.config))
    return True

  def car_api_available(self, email = None, password = None, charge = None):
    now = self.time.time()
    apiResponseDict = {}

    if(now - self.getCarApiLastErrorTime() < (self.getCarApiErrorRetryMins()*60)):
        # It's been under carApiErrorRetryMins minutes since the car API
        # generated an error. To keep strain off Tesla's API servers, wait
        # carApiErrorRetryMins mins till we try again. This delay could be
        # reduced if you feel the need. It's mostly here to deal with unexpected
        # errors that are hopefully transient.
        # https://teslamotorsclub.com/tmc/threads/model-s-rest-api.13410/page-114#post-2732052
        # says he tested hammering the servers with requests as fast as possible
        # and was automatically blacklisted after 2 minutes. Waiting 30 mins was
        # enough to clear the blacklist. So at this point it seems Tesla has
        # accepted that third party apps use the API and deals with bad behavior
        # automatically.
        self.debugLog(11, ': Car API disabled for ' +
                  str(int(self.getCarApiErrorRetryMins()*60 - (now - self.getCarApiLastErrorTime()))) +
                  ' more seconds due to recent error.')
        return False

    # Tesla car API info comes from https://timdorr.docs.apiary.io/
    if(self.getCarApiBearerToken() == '' or self.getCarApiTokenExpireTime() - now < 30*24*60*60):
        cmd = None
        apiResponse = b''

        # If we don't have a bearer token or our refresh token will expire in
        # under 30 days, get a new bearer token.  Refresh tokens expire in 45
        # days when first issued, so we'll get a new token every 15 days.
        if(self.getCarApiRefreshToken() != ''):

            cmd = 'curl -s -m 60 -X POST -H "accept: application/json" -H "Content-Type: application/json" -d \'' + \
                  self.json.dumps({'grant_type': 'refresh_token', \
                              'client_id': '81527cff06843c8634fdc09e8ac0abefb46ac849f38fe1e431c2ef2106796384', \
                              'client_secret': 'c7257eb71a564034f9419ee651c7d0e5f7aa6bfbd18bafb5c5c033b093bb2fa3', \
                              'refresh_token': self.getCarApiRefreshToken() }) + \
                  '\' "https://owner-api.teslamotors.com/oauth/token"'
        elif(email != None and password != None):
            cmd = 'curl -s -m 60 -X POST -H "accept: application/json" -H "Content-Type: application/json" -d \'' + \
                  self.json.dumps({'grant_type': 'password', \
                              'client_id': '81527cff06843c8634fdc09e8ac0abefb46ac849f38fe1e431c2ef2106796384', \
                              'client_secret': 'c7257eb71a564034f9419ee651c7d0e5f7aa6bfbd18bafb5c5c033b093bb2fa3', \
                              'email': email, 'password': password }) + \
                  '\' "https://owner-api.teslamotors.com/oauth/token"'

        if(cmd != None):
            if(self.config['config']['debugLevel'] >= 2):
                # Hide car password in output
                cmdRedacted = self.re.sub(r'("password": )"[^"]+"', r'\1[HIDDEN]', cmd)
                print('Car API cmd', cmdRedacted)
            apiResponse = self.run_process(cmd)
            # Example response:
            # b'{"access_token":"4720d5f980c9969b0ca77ab39399b9103adb63ee832014fe299684201929380","token_type":"bearer","expires_in":3888000,"refresh_token":"110dd4455437ed351649391a3425b411755a213aa815171a2c6bfea8cc1253ae","created_at":1525232970}'

        try:
            apiResponseDict = self.json.loads(apiResponse.decode('ascii'))
        except self.json.decoder.JSONDecodeError:
            pass

        try:
            self.debugLog(4, 'Car API auth response' + str(apiResponseDict))
            self.setCarApiBearerToken(apiResponseDict['access_token'])
            self.setCarApiRefreshToken(apiResponseDict['refresh_token'])
            self.setCarApiTokenExpireTime(now + apiResponseDict['expires_in'])
        except KeyError:
            self.debugLog(1, "ERROR: Can't access Tesla car via API.  Please log in again via web interface.")
            self.updateCarApiLastErrorTime()
            # Instead of just setting carApiLastErrorTime, erase tokens to
            # prevent further authorization attempts until user enters password
            # on web interface. I feel this is safer than trying to log in every
            # ten minutes with a bad token because Tesla might decide to block
            # remote access to your car after too many authorization errors.
            self.setCarApiBearerToken("")
            self.setCarApiRefreshToken("")

        self.master.saveSettings()

    if(self.getCarApiBearerToken() != ''):
        if(self.getVehicleCount() < 1):
            cmd = 'curl -s -m 60 -H "accept: application/json" -H "Authorization:Bearer ' + \
                  self.getCarApiBearerToken() + \
                  '" "https://owner-api.teslamotors.com/api/1/vehicles"'
            self.debugLog(8, 'Car API cmd', cmd)
            try:
                apiResponseDict = self.json.loads(self.run_process(cmd).decode('ascii'))
            except self.json.decoder.JSONDecodeError:
                pass

            try:
                self.debugLog(4, 'Car API vehicle list' + apiResponseDict + '\n')

                for i in range(0, apiResponseDict['count']):
                    self.addVehicle(apiResponseDict['response'][i]['id'])
            except (KeyError, TypeError):
                # This catches cases like trying to access
                # apiResponseDict['response'] when 'response' doesn't exist in
                # apiResponseDict.
                self.debugLog(1, "ERROR: Can't get list of vehicles via Tesla car API.  Will try again in "
                      + str(self.getCarApiErrorRetryMins()) + " minutes.")
                self.updateCarApiLastErrorTime()
                return False

        if(self.getVehicleCount() > 0):
            # Wake cars if needed
            needSleep = False
            for vehicle in self.getCarApiVehicles():
                if(charge == True and vehicle.stopAskingToStartCharging):
                    self.debugLog(8, "Don't charge vehicle " + str(vehicle.ID)
                              + " because vehicle.stopAskingToStartCharging == True")
                    continue

                if(now - vehicle.lastErrorTime < (self.getCarApiErrorRetryMins()*60)):
                    # It's been under carApiErrorRetryMins minutes since the car
                    # API generated an error on this vehicle. Don't send it more
                    # commands yet.
                    self.debugLog(8, "Don't send commands to vehicle " + str(vehicle.ID)
                              + " because it returned an error in the last "
                              + str(self.getCarApiErrorRetryMins()) + " minutes.")
                    continue

                if(vehicle.ready()):
                    continue

                if(now - vehicle.lastWakeAttemptTime <= vehicle.delayNextWakeAttempt):
                    self.debugLog(10, "car_api_available returning False because we are still delaying "
                              + str(delayNextWakeAttempt) + " seconds after the last failed wake attempt.")
                    return False

                # It's been delayNextWakeAttempt seconds since we last failed to
                # wake the car, or it's never been woken. Wake it.
                vehicle.lastWakeAttemptTime = now
                cmd = 'curl -s -m 60 -X POST -H "accept: application/json" -H "Authorization:Bearer ' + \
                      self.getCarApiBearerToken() + \
                      '" "https://owner-api.teslamotors.com/api/1/vehicles/' + \
                      str(vehicle.ID) + '/wake_up"'
                self.debugLog(8, 'Car API cmd', cmd)

                try:
                    apiResponseDict = self.json.loads(self.run_process(cmd).decode('ascii'))
                except self.json.decoder.JSONDecodeError:
                    pass

                state = 'error'
                try:
                    self.debugLog(4, 'Car API wake car response', apiResponseDict, '\n')

                    state = apiResponseDict['response']['state']

                except (KeyError, TypeError):
                    # This catches unexpected cases like trying to access
                    # apiResponseDict['response'] when 'response' doesn't exist
                    # in apiResponseDict.
                    state = 'error'

                if(state == 'online'):
                    # With max power saving settings, car will almost always
                    # report 'asleep' or 'offline' the first time it's sent
                    # wake_up.  Rarely, it returns 'online' on the first wake_up
                    # even when the car has not been contacted in a long while.
                    # I suspect that happens when we happen to query the car
                    # when it periodically awakens for some reason.
                    vehicle.firstWakeAttemptTime = 0
                    vehicle.delayNextWakeAttempt = 0
                    # Don't alter vehicle.lastWakeAttemptTime because
                    # vehicle.ready() uses it to return True if the last wake
                    # was under 2 mins ago.
                    needSleep = True
                else:
                    if(vehicle.firstWakeAttemptTime == 0):
                        vehicle.firstWakeAttemptTime = now

                    if(state == 'asleep' or state == 'waking'):
                        if(now - vehicle.firstWakeAttemptTime <= 10*60):
                            # http://visibletesla.com has a 'force wakeup' mode
                            # that sends wake_up messages once every 5 seconds
                            # 15 times. This generally manages to wake my car if
                            # it's returning 'asleep' state, but I don't think
                            # there is any reason for 5 seconds and 15 attempts.
                            # The car did wake in two tests with that timing,
                            # but on the third test, it had not entered online
                            # mode by the 15th wake_up and took another 10+
                            # seconds to come online. In general, I hear relays
                            # in the car clicking a few seconds after the first
                            # wake_up but the car does not enter 'waking' or
                            # 'online' state for a random period of time. I've
                            # seen it take over one minute, 20 sec.
                            #
                            # I interpret this to mean a car in 'asleep' mode is
                            # still receiving car API messages and will start
                            # to wake after the first wake_up, but it may take
                            # awhile to finish waking up. Therefore, we try
                            # waking every 30 seconds for the first 10 mins.
                            vehicle.delayNextWakeAttempt = 30;
                        elif(now - vehicle.firstWakeAttemptTime <= 70*60):
                            # Cars in 'asleep' state should wake within a
                            # couple minutes in my experience, so we should
                            # never reach this point. If we do, try every 5
                            # minutes for the next hour.
                            vehicle.delayNextWakeAttempt = 5*60;
                        else:
                            # Car hasn't woken for an hour and 10 mins. Try
                            # again in 15 minutes. We'll show an error about
                            # reaching this point later.
                            vehicle.delayNextWakeAttempt = 15*60;
                    elif(state == 'offline'):
                        if(now - vehicle.firstWakeAttemptTime <= 31*60):
                            # A car in offline state is presumably not connected
                            # wirelessly so our wake_up command will not reach
                            # it. Instead, the car wakes itself every 20-30
                            # minutes and waits some period of time for a
                            # message, then goes back to sleep. I'm not sure
                            # what the period of time is, so I tried sending
                            # wake_up every 55 seconds for 16 minutes but the
                            # car failed to wake.
                            # Next I tried once every 25 seconds for 31 mins.
                            # This worked after 19.5 and 19.75 minutes in 2
                            # tests but I can't be sure the car stays awake for
                            # 30secs or if I just happened to send a command
                            # during a shorter period of wakefulness.
                            vehicle.delayNextWakeAttempt = 25;

                            # I've run tests sending wake_up every 10-30 mins to
                            # a car in offline state and it will go hours
                            # without waking unless you're lucky enough to hit
                            # it in the brief time it's waiting for wireless
                            # commands. I assume cars only enter offline state
                            # when set to max power saving mode, and even then,
                            # they don't always enter the state even after 8
                            # hours of no API contact or other interaction. I've
                            # seen it remain in 'asleep' state when contacted
                            # after 16.5 hours, but I also think I've seen it in
                            # offline state after less than 16 hours, so I'm not
                            # sure what the rules are or if maybe Tesla contacts
                            # the car periodically which resets the offline
                            # countdown.
                            #
                            # I've also seen it enter 'offline' state a few
                            # minutes after finishing charging, then go 'online'
                            # on the third retry every 55 seconds.  I suspect
                            # that might be a case of the car briefly losing
                            # wireless connection rather than actually going
                            # into a deep sleep.
                            # 'offline' may happen almost immediately if you
                            # don't have the charger plugged in.
                    else:
                        # Handle 'error' state.
                        if(now - vehicle.firstWakeAttemptTime <= 60*60):
                            # We've tried to wake the car for less than an
                            # hour.
                            foundKnownError = False
                            if('error' in apiResponseDict):
                                error = apiResponseDict['error']
                                for knownError in self.getCarApiTransientErrors():
                                    if(knownError == error[0:len(knownError)]):
                                        foundKnownError = True
                                        break

                            if(foundKnownError):
                                # I see these errors often enough that I think
                                # it's worth re-trying in 1 minute rather than
                                # waiting 5 minutes for retry in the standard
                                # error handler.
                                vehicle.delayNextWakeAttempt = 60;
                            else:
                                # by the API servers being down, car being out of
                                # range, or by something I can't anticipate. Try
                                # waking the car every 5 mins.
                                vehicle.delayNextWakeAttempt = 5*60;
                        else:
                            # Car hasn't woken for over an hour. Try again
                            # in 15 minutes. We'll show an error about this
                            # later.
                            vehicle.delayNextWakeAttempt = 15*60;

                    if(state == 'error'):
                        self.debugLog(1, ": Car API wake car failed with unknown response.  " \
                                "Will try again in "
                                + str(vehicle.delayNextWakeAttempt) + " seconds.")
                    else:
                        self.debugLog(1, "Car API wake car failed.  State remains: '"
                                + state + "'.  Will try again in "
                                + str(vehicle.delayNextWakeAttempt) + " seconds.")

                if(vehicle.firstWakeAttemptTime > 0
                   and now - vehicle.firstWakeAttemptTime > 60*60):
                    # It should never take over an hour to wake a car.  If it
                    # does, ask user to report an error.
                    self.debugLog(1, "ERROR: We have failed to wake a car from '"
                        + state + "' state for %.1f hours.\n" \
                          "Please private message user CDragon at " \
                          "http://teslamotorsclub.com with a copy of this error. " \
                          "Also include this: %s" % (
                          ((now - vehicle.firstWakeAttemptTime) / 60 / 60),
                          str(apiResponseDict)))

    if(now - self.getCarApiLastErrorTime() < (self.getCarApiErrorRetryMins()*60) or self.getCarApiBearerToken() == ''):
        self.debugLog(8, "car_api_available returning False because of recent carApiLasterrorTime "
                + str(now - self.getCarApiLastErrorTime()) + " or empty carApiBearerToken '"
                + self.getCarApiBearerToken() + "'")
        return False

    # We return True to indicate there was no error that prevents running
    # car API commands and that we successfully got a list of vehicles.
    # True does not indicate that any vehicle is actually awake and ready
    # for commands.
    self.debugLog(8, "car_api_available returning True")

    if(needSleep):
        # If you send charge_start/stop less than 1 second after calling
        # update_location(), the charge command usually returns:
        #   {'response': {'result': False, 'reason': 'could_not_wake_buses'}}
        # I'm not sure if the same problem exists when sending commands too
        # quickly after we send wake_up.  I haven't seen a problem sending a
        # command immediately, but it seems safest to sleep 5 seconds after
        # waking before sending a command.
        self.time.sleep(5);

    return True

  def debugLog(self, minlevel, message):
    if (self.debugLevel >= minlevel):
      print("TeslaAPI: (" + str(minlevel) + ") " + message)

  def getCarApiBearerToken(self):
    return self.carApiBearerToken

  def getCarApiErrorRetryMins(self):
    return self.carApiErrorRetryMins

  def getCarApiLastErrorTime(self):
    return self.carApiLastErrorTime

  def getCarApiRefreshToken(self):
    return self.carApiRefreshToken

  def getCarApiTransientErrors(self):
    return self.carApiTransientErrors

  def getCarApiTokenExpireTime(self):
    return self.carApiTokenExpireTime

  def getLastStartOrStopChargeTime(self):
    return int(self.carApiLastStartOrStopChargeTime)

  def getVehicleCount(self):
    # Returns the number of currently tracked vehicles
    return int(len(self.carApiVehicles))

  def getCarApiVehicles(self):
    return self.carApiVehicles

  def setCarApiBearerToken(self, token=None):
    if token:
      self.carApiBearerToken = token
      return True
    else:
      return False

  def setCarApiErrorRetryMins(self, mins):
    self.carApiErrorRetryMins = mins
    return True

  def setCarApiRefreshToken(self, token):
    self.carApiRefreshToken = token
    return True

  def setCarApiTokenExpireTime(self, value):
    self.carApiTokenExpireTime = value
    return True

  def setMaster(self, master):
    self.master = master
    return True

  def updateCarApiLastErrorTime(self):
    self.carApiLastErrorTime = self.time.time()
    return True

  def updateLastStartOrStopChargeTime(self):
    self.carApiLastStartOrStopChargeTime = self.time.time()
    return True

class CarApiVehicle:

    import time

    carapi     = None
    config     = None
    debuglevel = 0
    ID         = None

    firstWakeAttemptTime = 0
    lastWakeAttemptTime = 0
    delayNextWakeAttempt = 0

    lastErrorTime = 0
    stopAskingToStartCharging = False
    lat = 10000
    lon = 10000

    def __init__(self, ID, carapi, config):
        self.carapi     = carapi
        self.config     = config
        self.debugLevel = config['config']['debugLevel']
        self.ID         = ID

    def debugLog(self, minlevel, message):
      if (self.debugLevel >= minlevel):
        print("TeslaAPIVehicle: (" + str(minlevel) + ") " + message)

    def ready(self):
        if(self.time.time() - self.lastErrorTime < (self.carApiErrorRetryMins*60)):
            # It's been under carApiErrorRetryMins minutes since the car API
            # generated an error on this vehicle. Return that car is not ready.
            debugLog(8, ': Vehicle ' + str(self.ID)
                    + ' not ready because of recent lastErrorTime '
                    + str(self.lastErrorTime))
            return False

        if(self.firstWakeAttemptTime == 0 and self.time.time() - self.lastWakeAttemptTime < 2*60):
            # Less than 2 minutes since we successfully woke this car, so it
            # should still be awake.  Tests on my car in energy saver mode show
            # it returns to sleep state about two minutes after the last command
            # was issued.  Times I've tested: 1:35, 1:57, 2:30
            return True

        debugLog(8, ': Vehicle ' + str(self.ID) + " not ready because it wasn't woken in the last 2 minutes.")
        return False

    def run_process(self, cmd):
        result = None
        try:
            result = subprocess.check_output(cmd, shell=True)
        except subprocess.CalledProcessError:
            # We reach this point if the process returns a non-zero exit code.
            result = b''

        return result

    def update_location(self):
        if(self.ready() == False):
            return False

        apiResponseDict = {}

        cmd = 'curl -s -m 60 -H "accept: application/json" -H "Authorization:Bearer ' + \
              self.getCarApiBearerToken() + \
              '" "https://owner-api.teslamotors.com/api/1/vehicles/' + \
              str(self.ID) + '/data_request/drive_state"'

        # Retry up to 3 times on certain errors.
        for retryCount in range(0, 3):
            debugLog(8, ': Car API cmd' + cmd)
            try:
                apiResponseDict = self.json.loads(run_process(cmd).decode('ascii'))
                # This error can happen here as well:
                #   {'response': {'reason': 'could_not_wake_buses', 'result': False}}
                # This one is somewhat common:
                #   {'response': None, 'error': 'vehicle unavailable: {:error=>"vehicle unavailable:"}', 'error_description': ''}
            except self.json.decoder.JSONDecodeError:
                pass

            try:
                debugLog(4, ': Car API vehicle GPS location' + apiResponseDict + '\n')

                if('error' in apiResponseDict):
                    foundKnownError = False
                    error = apiResponseDict['error']
                    for knownError in self.getCarApiTransientErrors():
                        if(knownError == error[0:len(knownError)]):
                            # I see these errors often enough that I think
                            # it's worth re-trying in 1 minute rather than
                            # waiting carApiErrorRetryMins minutes for retry
                            # in the standard error handler.
                            debugLog(1, "Car API returned '" + error
                                      + "' when trying to get GPS location.  Try again in 1 minute.")
                            self.time.sleep(60)
                            foundKnownError = True
                            break
                    if(foundKnownError):
                        continue

                response = apiResponseDict['response']

                # A successful call to drive_state will not contain a
                # response['reason'], so we check if the 'reason' key exists.
                if('reason' in response and response['reason'] == 'could_not_wake_buses'):
                    # Retry after 5 seconds.  See notes in car_api_charge where
                    # 'could_not_wake_buses' is handled.
                    self.time.sleep(5)
                    continue
                self.lat = response['latitude']
                self.lon = response['longitude']
            except (KeyError, TypeError):
                # This catches cases like trying to access
                # apiResponseDict['response'] when 'response' doesn't exist in
                # apiResponseDict.
                debugLog(1, ": ERROR: Can't get GPS location of vehicle " + str(self.ID) + \
                          ".  Will try again later.")
                self.lastErrorTime = self.time.time()
                return False

            return True
