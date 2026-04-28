
# March 4th 2026 - 00:14

## Issue 1; (old bug actually, don't know why it came back): When a stream is frozen (still image), fullscreen close button no longer fires. Have to refresh browser, no other exit possible. Not ok

## Issue 2: still can't overwrite presets in UI

 We started working on this in another chat but I lost track (too many chats open). test executed on KITCHEN OFFICE ID: T8419P0024110C6A. It's a Eufy-only issue (confirmed by successfully testing feature on 95270001NT3KNA67)

## Issue 3: Stream type switching, UI functionality: seems to be having apparent random issues; works in some cases, not others

image 2: switched from functioning MJPEG to WebRTC. Getting as you can see in image 3: "Restart failed: MJPEG does not support restart.

So,

1. MJPEG should support restart.
2. We have different MJPEG implementations, if I remember correctly: some are direct cgi endpoints (I think it's Amcrest or maybe even Reolink), some are backend/MediaMTX managed, tapping into existing RTSP stream.

However, at some point we homogenized this into all living in MediaMTX due to the 1 camera = 1 input stream policy; Let me know if my memory is correct. But that would mean MJPEG COULD be restarted, at least its backend's RTSP input streamer. Analyze this to clear out the field before we take any action on this matter.

3. Seems that we don't have the proper "create a new path" logic when switching from MJPEG to WebRTC. 

I thought we addressed that in a recent past: ensuring that MediaMTX has a path for ALL CAMERAS, although insuring it's not pulling (no active ffmpeg process) if camera is in solo-MJPEG mode (native .cgi on the camera or using baichuan, neolink, I'm not sure any longer how all of this is setup, so please verify)

## Issue 4: very bad UI in iPhone

(using max format) and some issues in iPads. We need a much, much more powerful mobile UI

## Issue 5 (related): Bad, very bad on older iPads

streams get systematically stuck within less than the sheduled hourly reload span

## Issue 6: In general, lots of streams end up stuck but it's hard now to detect/visualize. 

I kind of cheated my way out when We implemented a still image capture to show instead of any error or black stream. That's not ok. When black screen or still frame detected, instead of replacing with still snapshot, let's have a real message with some relevant details on what's happening under the hood

Considerations: 
    a. We already have the "Signal Lost" detection that seems to be working well.
    b. We could add new categories, better matching why a stream is still, with relevant logos. Backend should provide analysis - UI should show "Analyzing..." or something more elegantly phrased, then show results as a sentence or at least some clear indication of what's going on. Why not, even, a virtual terminal inside the stream, as an optional feature dependent on the existing "Quiet Status Messages" setting, and, yet, still have a debug button that could open/close this terminal (shall be a separate superposed modal, no backdrop event exit, a proper "copy" button to copy all the content in the terminal, an proper memory, management of retention depending on device capabilities) at all times (even when all ok): we need log classification per camera in the database, maybe? Any log for camera XXXX always redirects to a specific logging cache/table (frequently vacuumed: every 2 hours or so?) or even a dedicated file with proper redirection logic: not sure our use of the native logger lib can allow for this, you'll let me know. We have, however, a relatively interresting customized logging library in Alerts-Module (officewsl or aws1). But best if logger lib can handle such modularized logging (without loosing info from main logs: think of it as a bash | tee -a filename, type of thing.

## Issue 7: We need a way to properly assess a device's capability

The U.I. needs to better assess (might want the UI to send data to analyze directly to the backend and let the analysis be done there) device type and throttle down quality to favor performance, reliability and non-stuck streams. I guess this could be handled in partnership with the existing watchdog. 

A learning pattern would be ideal. We could use our own AI with neural trial and error structure and storage through a transformer. We could also "partner" with CLAUDE API (use: s"ource ~/.bashrc && list_aws_secrets 1" and have fun with updating start.sh to load required secrets if not already included in current logic)

That means database must store existing/known clients. Can't use MAC address with iOS... they now have good security against that, as a default. We need to find a way to identify a device, thouhg. Could be requesting user to name the device if not named: local storage stores name + database. Matching can happen on each new reload. Or cookies for long term. Or other way that you know of that would be much more reliable: anything JS can read from the device it's running from that can ensure unicity? And requesting user as a fallback, knowing it has limitations => so a known existing device that didn't connect in whole year is removed from db: not sure how SQL handles that: a stored procedure that runs on each new upsert, I suppose, could easily take care of that. 

To accomplish This very long set of tasks properly, you have to:

1. Abide by CLAUDE.md rules (and please, read them all).
2. Read the README_project_history.md extensively enough so as to not waste time undoing something accidentally, no matter the token cost: try to be as efficient as possible, use your transformer-based architecture (if I'm correct) to tokenise this document into as many dimentions as needed. If you can't do it from here, maybe get some help from the 0_ANAMNESIS project, although not sure it's advanced enough yet.

3. Be cool and critical. This architecture is becoming heavy and probably needs some cleaning, better OOP and less accidentally generated overhead. No hardcoding etc. 

4. The issues number are the order of priority. 

5. 1 issue = 1 featured branch. 

6. Memoize this document as your main guide for the days to come. 

7. Suggest work division: I think it'd be worth using several Claude chats/agents at the same time, providing they properly communicate autonomously and use the intercomm to let every other one know what they're doing. Or I let you manage proper parallelization in one single chat/agent, which you seem to be able to handle to a certain level. 

8. Work and decide about point 7 from within reasonnable capabilities thresholds that you only really know. 
