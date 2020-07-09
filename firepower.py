import requests
import json
import urllib3
from pathlib import Path
from time import sleep
from ipaddress import IPv4Network

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

####### Option load:
optionFile = Path(__file__).parent / './options.json'
with open(optionFile, "rb") as opt:
    options = json.load(opt)['firepower']
    USERNAME = options['username']
    PASSWORD = options['password']
    FDM = options['address']
    ROUTE = options['failover_route']
    GATEWAY = options['failover_gateway']
    FO_INTERFACE = options['failover_interface']
#######

oauth_data = {
    "grant_type": "password",
    "username": USERNAME,
    "password": PASSWORD
}

headers = {
          "Content-Type": "application/json",
          "Accept": "application/json"
}
baseurl = "https://" + FDM + "/api/fdm/latest"


class FirePower():

    def __init__(self):
        # Set up HTTP session & get auth token / virtual router ID
        with requests.Session() as self.s:
            self.token = self.authRequest()
            self.getGlobalVR()

    def authRequest(self):
        # Authenticate to FDM and retrieve token
        authurl = baseurl + "/fdm/token"
        print("Posting AUTH request to FDM")
        resp = self.s.post(authurl, headers=headers,
                           data=json.dumps(oauth_data),
                           verify=False)
        if resp.status_code == 200:
            print("Auth success - got token.")
            return json.loads(resp.text)['access_token']
        else:
            print("Authentication Failed.")
            print(resp.text)

    def getGlobalVR(self):
        # Grab list of virtual routers
        print("Pulling list of virtual routers...")
        vr_url = "/devices/default/routing/virtualrouters"
        resp = self.getData(vr_url)
        # Parse for Global virtual router
        print("Got virtual routers - finding global vrouter...")
        vrouters = json.loads(resp)['items'][0]
        if vrouters['name'] == "Global":
            print("Got Global routing table, ID: " + vrouters['id'])
            self.globalVR = vrouters["id"]

    def addRoute(self):
        # Function to add new route to routing table
        print("Getting current route table...")
        # First we check to make sure the route already exists,
        # otherwise no work is necessary
        route = self.doesRouteExist()
        if route:
            # If our backup route already exists, make no changes
            print("Already in failover state. \
                  No changes made to routing table")
            return False
        else:
            # If not already failed over - proceed to add backup route
            print("No backup route currently in routing table. \
                   Adding backup route...")
            # First create a route object - a collection of data required
            # to add a static route entry
            route_data = self.createRouteObject()
            add_url = "/devices/default/routing/virtualrouters/" + \
                      self.globalVR + "/staticrouteentries"
            print("CREATE: Route object")
            # Post to route creation API
            self.postData(add_url, route_data)
            print("Static route added")
            # Deploy config changes
            if self.deployPolicy() is True:
                print("Route successfully added & changes deployed.")
                return True
            else:
                print("ERROR: Deployment error. Route may not be added.")
                return False

    def delRoute(self):
        # Function to delete static route from routing table
        print("Getting current route table...")
        # First we check to make sure the route already exists,
        # otherwise no work is necessary
        route = self.doesRouteExist()
        if route is False:
            # If no route is found, then no changes are needed
            print("Not currently in failover state. \
                  No changes made to routing table")
            return False
        else:
            # If we found our backup route, then proceed to removal
            del_url = "/devices/default/routing/virtualrouters/" + \
                      self.globalVR + "/staticrouteentries/" + route['id']
            # Send DELETE to route object, then deploy policy changes
            if self.deleteData(del_url) is True:
                print("Static route deleted.")
                if self.deployPolicy() is True:
                    print("Route successfully removed & changes deployed.")
                    return True
                else:
                    print("ERROR: Deployment error. Route may not be removed.")
                    return False

    def deployPolicy(self):
        # Policy deployment & status checking
        url = "/operational/deploy"
        print("Beginning change deployment")
        # Send POST request, which starts deployment. Grab ID to check status
        deploymentID = json.loads(self.postData(url))['id']
        print("Changes being deployed... Deployment ID: " + deploymentID)
        deployed = False
        while deployed is False:
            # Deployment is not instant - we will give it a
            # few seconds between checks
            # NOTE: Can take a long time depending on appliance
            #       resources & number of changes
            sleep(8)
            print("Checking Deployment Status...")
            # Grab current deployment task list
            taskList = json.loads(self.getData(url))
            # Search for our deployment task by ID
            for task in taskList['items']:
                # Check the status of our deployment
                if task['id'] == deploymentID and task['state'] == 'DEPLOYED':
                    print("Deployment status is: " + task['state'])
                    deployed = True
                    return True
                elif task['id'] == deploymentID and task['state'] != 'DEPLOYED':
                    # If changes not yet deployed, check again momentarily
                    print("Deployment status is: " + task['state'])
                    deployed = False

    def doesRouteExist(self):
        # Pull current routing table and look for our backup route
        current_routes = self.getRoutes()
        # If no routes exist, skip everything
        if current_routes == []:
            return False
        # Iterate through all routes to find our specific backup route
        for route in current_routes:
            gateway = self.getNetworkObject(route['gateway']['id'])
            dest_network = self.getNetworkObject(route['networks'][0]['id']).split('/')[0]
            # Match based on route prefix & upstream next hop gateway
            if gateway == GATEWAY and dest_network == ROUTE.split('/')[0]:
                print("Found route to %s via %s" % (dest_network, gateway))
                return route
        # Return false if we don't find anything
        return False

    def getNetworkObject(self, id):
        # Get network object IP Address by known ID
        host_url = "/object/networks/" + id
        netobj = self.getData(host_url)
        return json.loads(netobj)['value']

    def getDuplicateObject(self, name):
        # Get object ID by known object name
        host_url = "/object/networks?filter=name%3A" + name
        netobj = self.getData(host_url)
        return json.loads(netobj)['items'][0]['id']

    def getFailoverInterface(self, failover_interface):
        # Get interface ID
        iface_url = "/devices/default/interfaces"
        ifaceList = self.getData(iface_url)
        for iface in json.loads(ifaceList)['items']:
            # Iterate through all interfaces to find physical port name
            if iface['hardwareName'] == failover_interface:
                return iface['id'], iface['name']

    def createGateway(self, address):
        # Send request to createNetworkObject with gateway specific parameters
        host_name = "gateway_" + str(address)
        gateway = self.createNetworkObject(host_name, 'HOST', address)
        return gateway, host_name

    def createNetwork(self, address):
        # Send request to createNetworkObject with network specific parameters
        # NOTE: Firepower naming cannot include special characters..
        #       so need to remove the CIDR prefix
        host_name = "network_" + str(address.split('/')[0])
        # NOTE: Via API - FDM requires full netmask, no CIDR
        address = str(IPv4Network(address).network_address) + \
            "/" + str(IPv4Network(address).netmask)
        netobj = self.createNetworkObject(host_name, 'NETWORK', address)
        return netobj, host_name

    def createRouteObject(self):
        # Collect required data:
        iface_ID, iface_name = self.getFailoverInterface(FO_INTERFACE)
        network_ID, network_name = self.createNetwork(ROUTE)
        gateway_ID, gateway_name = self.createGateway(GATEWAY)
        # Generate dict of required values to create static route object
        routeobject = {}
        routeobject['name'] = "route_BACKUP"
        routeobject['description'] = "Created by ISP Failover automation"
        routeobject['iface'] = {}
        routeobject['iface']['id'] = iface_ID
        routeobject['iface']['type'] = "physicalinterface"
        routeobject['iface']['name'] = iface_name
        routeobject['networks'] = [{}]
        routeobject['networks'][0] = {}
        routeobject['networks'][0]['id'] = network_ID
        routeobject['networks'][0]['type'] = "networkobject"
        routeobject['networks'][0]['name'] = network_name
        routeobject['gateway'] = {}
        routeobject['gateway']['id'] = gateway_ID
        routeobject['gateway']['type'] = "networkobject"
        routeobject['gateway']['name'] = gateway_name
        routeobject['metricValue'] = 1
        routeobject['ipType'] = "IPv4"
        routeobject['type'] = "staticrouteentry"
        return routeobject

    def createNetworkObject(self, name, subtype, address):
        # Create a new network object
        host_url = "/object/networks"
        # Define host details
        host_data = {}
        host_data['name'] = name
        host_data['description'] = "Created by ISP Failover automation"
        host_data['subType'] = subtype
        host_data['value'] = address
        host_data['type'] = "networkobject"
        # Post to object creation API
        print("CREATE: Network Object: " + str(address))
        netobj = self.postData(host_url, host_data)
        try:
            # Check to see if we received duplicate object error
            if json.loads(netobj)['error']['messages'][0]['code'] == "duplicateName":
                # If already created... just go find existing object ID
                dupID = self.getDuplicateObject(name)
                print("Found duplicate ID: " + dupID)
                return dupID
            else:
                # If object created, return object ID
                print("NEW ObjectID: " + json.loads(netobj)['id'])
                return json.loads(netobj)['id']
        except KeyError:
            return json.loads(netobj)['id']

    def getRoutes(self):
        # Grab list of ALL static route entries
        route_url = "/devices/default/routing/virtualrouters/" + \
                    self.globalVR + "/staticrouteentries"
        route_data = self.getData(route_url)
        return json.loads(route_data)['items']

    def getData(self, url):
        # General function for HTTP GET requests with authentication token
        headers = {
              "Content-Type": "application/json",
              "Authorization": "Bearer " + self.token
        }
        get_url = baseurl + url
        resp = self.s.get(get_url, headers=headers, verify=False)
        if resp.status_code == 200:
            return resp.text
        else:
            print("Request FAILED. " + str(resp.status_code))
            print(resp.text)

    def deleteData(self, url):
        # General function for HTTP DELETE requests
        headers = {
              "Content-Type": "application/json",
              "Authorization": "Bearer " + self.token
        }
        get_url = baseurl + url
        resp = self.s.delete(get_url, headers=headers, verify=False)
        if resp.status_code == 204:
            return True
        else:
            print("Request FAILED. " + str(resp.status_code))
            return False

    def postData(self, url, payload=None):
        # General function for HTTP POST requests with authentication token
        headers = {
              "Content-Type": "application/json",
              "Authorization": "Bearer " + self.token
        }
        get_url = baseurl + url
        resp = self.s.post(get_url, headers=headers, verify=False,
                           json=payload)
        if resp.status_code == 200:
            return resp.text
        if resp.status_code == 422:
            print("Object already exists")
            return resp.text
        else:
            print("Request FAILED. " + str(resp.status_code))
            print(resp.text)


if __name__ == "__main__":
    FirePower()
