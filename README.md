# AmpliPi Plugin for Home Assistant

This integration is used to integrate with [AmpliPi](https://github.com/micro-nova/AmpliPi), the open-source software of [AmpliPro whole-home audio systems](https://www.amplipro.com/).

AmpliPro is a line of whole-home multi-zone audio systems created by [Micro-Nova](https://www.micro-nova.com/), the same people who upkeep this integration. It can play up to 4 different audio streams to any number of connected zones (of which each system has 6-36, depending on the number of expansion units) and control the volume on a per zone basis, this integration aims to make you never need to touch the [app](https://www.amplipi.com/app) or webapp again by ultimately providing all the functionality* that those interfaces provide, along with the added benefit of automations that allow you to streamline the process of connecting your music to your speakers or making the PA system more user friendly by leveraging home assistant's default support of Google's text-to-speech.


*see known limitations section for exceptions, most of which are being ironed out with time

## Installation

### Standard Installation

1. Ensure that [HACS](https://hacs.xyz) is installed.
1. Navigate to HACS on the sidebar and open the HACS settings by selecting the three dots icon. From there select "custom repositories".
![Step 2](doc_img/customrepo.png)
1. A dialog box should appear. In it, paste a link to to this repo, found at `https://github.com/micro-nova/hacs_amplipi`, under "Repository." Under "Category," select "Integration." Then click "Add."
![Step 3](doc_img/add.png)
1. This will add the AmpliPi repository to your version of the HACS store! Search for it in the search bar and then click on it when it pops up.
![Step 4](doc_img/store.png)
1. On the store page, click "Download" to install the integration.
![Step 5](doc_img/download.png)
1. After the integration finishes installing, you will need to restart your Home Assistant. To do this, navigate to your Home Assistant's settings on the sidebar, then click the "Restart required." Your HomeAssistant will then reboot.
![Step 6](doc_img/restart.png)
1. **AmpliPi** integration should auto-discover your AmpliPi, and prompt you to configure the integration. If this isn't the case, see the basic configuration guide below.


### Manual Installation

In case you would like to install manually:

1. Copy the folder `custom_components/amplipi` to `custom_components` in your Home Assistant `config` folder.
2. **AmpliPi** integration should auto-discover your AmpliPi, and prompt you to configure the integration. If this isn't the case, see the basic configuration guide below.

### Basic Configuration Guide

When configuring the integration, the text boxes should be automatically populated by network discovery; if that isn't the case, here's the guide to filling them out manually:

- Host: The IP of the device running AmpliPi. If you're using an AmpliPro device, this can be found on the display on the front of the unit

- Port: The port of the AmpliPi API. If using an AmpliPro device, this will always be port 80

## Optional Setup

This component has an optional companion component that can be found at https://github.com/micro-nova/AmpliPi-HomeAssistant-Card if you wish to use home assistant as a ui for your AmpliPi software. You can install that by first installing the [MiniMediaPlayer](https://github.com/kalkih/mini-media-player) component which can be found by searching for it in the HACS searchbar, and then following the same installation guide as this component but replacing the repository link with https://github.com/micro-nova/AmpliPi-HomeAssistant-Card and with type "Dashboard"

## Removal Instructions

This integration is easy to uninstall by simply going to Settings -> Devices & Services -> AmpliPi and clicking the three dots on the "integration entries" option
You can uninstall the blueprints by going to Settings -> Automations & Scenes -> Blueprints and then hitting the three dots and deleting the relevant blueprints

If you followed the optional setup, you can uninstall both MiniMediaPlayer and our cards by finding them in HACS and doing the same three dots -> delete option as with everything else

## Data Updates

This integration polls the AmpliPi API every 2 seconds (the same rate as the app and webapp) and also updates data whenever a state-returning api call is made, which includes most state-changing functions such as play/pause, on/off, volume changes, etc.

## Example Automations

### Start Streaming
This integration is for a whole-home audio system, in most cases you're likely to just want to play some music to some number of zones or groups at a specified volume. Luckily, we have a blueprint that does just that that installs itself alongside the integration itself
That particular integration doesn't provide any triggers, but the opportunites are endless, here's just a few examples: 
- Play music to a room when a motion detector notices someone is there (or disconnects when there isn't)
- Connect your TV's audio output to go to the surround sound speakers when you're watching something
- Set up a jukebox using RFID cards that are associated with a specific stream

### Make PA Announcements
You can send a text-to-speech message to the PA system using automations as well, just use the `Text-to-speech (TTS) 'Speak'` automation type with the google translate text to speech entity as a target on the Announce media player entity and type whatever message you'd like
To simplify this process, we've made a blueprint that pre-populates the two related entities so all you need to do is type a message and decide if it should be cached. Similarly to the Start Streaming blueprint, this blueprint doesn't come with any triggers, though here's a few examples of what you could do:
- Place a button in the kitchen to be pressed when dinner is ready to let the whole family know
- Set a time trigger so that you don't lose track of time, such as reminding you to go to bed or do some chore
- Use with other integrations to make more specialty announcements, such as warning you when a calendar event is coming up

## Supported Devices

Broadly, the devices that are supported are any devices AmpliPro that run the AmpliPi software.

### Supported
The following devices are supported by this integration:
- [AmpliPro Controller](https://www.amplipro.com/product-page/amplipro-home-audio-controller)
- [AmpliPro Streamer](https://www.amplipro.com/product-page/amplipro-streamer-4)
- [AmpliPro Expansion Unit](https://www.amplipro.com/product-page/amplipro-zone-expander)*

*With use of an AmpliPro Controller unit, "support" in this case means "can handle up to 36 zones as are provided by the controller unit

### Not Supported
The following devices are not supported by this integration:
- [AmpliPro Wall Panel](https://www.amplipro.com/product-page/amplipro-wall-panel)

## Supported Functionality

### Entities

This integration provides the following entities:

#### Media Players
- 4 x Sources
- 6-36 x Zones (depending on how many AmpliPro Expansion Units are connected to your Controller)
- Any number of Groups*
- Any number of Streams*
- 1 x Announcement/PA system

*as your home assistant's storage allows
## Known Limitations

AmpliPi devices do not report a distinct identifier, so this integration currently only supports one controller per installation.

This integration does not currently support creating groups or streams as you would do from the AmpliPi settings page

## Troubleshooting
This section of the guide will be expanded in future updates as we find common issues and their solutions. In the meantime, contact us on our [discourse](https://amplipi.discourse.group/c/home-automation-integration) or at [support@micro-nova.com](mailto:support@micro-nova.com)

## Credits

Cursor graphics used in this document from [Freepik](https://www.freepik.com/).