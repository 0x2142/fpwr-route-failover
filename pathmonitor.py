import firepower
from pythonping import ping
import sys
from time import sleep
import json
from pathlib import Path

####### Option load:
optionFile = Path(__file__).parent / './options.json'
with open(optionFile, "rb") as opt:
    options = json.load(opt)['pingtest']
DEFAULT_DESTINATION = options['target']
PING_COUNT = options['count']
MAX_LATENCY = options['max_latency']
MAX_LOSS = options['max_loss']
#######


def doPing():
    # Run ping against target destination
    try:
        print("Checking loss/latency to " + DEFAULT_DESTINATION)
        result = ping(DEFAULT_DESTINATION, size=2, count=PING_COUNT,
                      verbose=True)
    except PermissionError:
        # must have privileges to ping
        print("Error: User not root! Cannot open socket")
        sys.exit(0)
    sleep(1)
    print("Calculating results...")
    loss = calculateLoss(result)
    print("AVG response time: " + str(result.rtt_avg_ms) +
          "ms. Loss = " + str(loss) + "%")
    print("Target response time: <" + str(MAX_LATENCY) +
          "ms. Target Loss: <" + str(MAX_LOSS))
    return result.rtt_avg_ms, loss


def calculateLoss(result):
    # Take in ping results, parse for loss data
    lost = 0
    for packet in result:
        if "Reply from" in str(packet):
            pass
        else:
            lost += 1
    if lost != 0:
        lossperc = 100 * lost / PING_COUNT
        return lossperc
    elif lost == 0:
        return 0


def checkMetrics(response, loss):
    # Simple check if our responses were under the expected thresholds
    if response >= MAX_LATENCY or loss >= MAX_LOSS:
        print("Loss/Latency violate thresholds.")
        return True
    else:
        print("Loss/Latency within thresholds.")
        return False


def run():
    # Gather loss / latency data
    response, loss = doPing()
    # Check if it exceeds thresholds
    failover = checkMetrics(response, loss)
    # IF loss/latency outside of thresholds,
    # add default route to second provider
    fw = firepower.FirePower()
    if failover is True:
        result = fw.addRoute()
        if result is True:
            print("TRAFFIC SUCCESSFULLY FAILED OVER TO BACKUP CONNECTION")
    # IF loss/latency is healthy,
    # remove default route to second provider
    elif failover is False:
        result = fw.delRoute()
        if result is True:
            print("TRAFFIC SUCCESSFULLY ROUTED BACK TO PRIMARY CONNECTION")


if __name__ == "__main__":
    run()
