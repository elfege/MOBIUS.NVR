---
title: "REOLINK ONVIF Topics"
layout: default
---

Available topics:
<tns1:VideoSource xmlns:tns1="http://www.onvif.org/ver10/topics"
    xmlns:wstop="http://docs.oasis-open.org/wsn/t-1"
    xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
    xmlns:SOAP-ENC="http://www.w3.org/2003/05/soap-encoding"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
    xmlns:wsdd="http://schemas.xmlsoap.org/ws/2005/04/discovery"
    xmlns:wsa5="http://www.w3.org/2005/08/addressing" xmlns:xmime="http://tempuri.org/xmime.xsd"
    xmlns:xmime5="http://www.w3.org/2005/05/xmlmime"
    xmlns:xop="http://www.w3.org/2004/08/xop/include"
    xmlns:wsrfbf="http://docs.oasis-open.org/wsrf/bf-2" xmlns:tt="http://www.onvif.org/ver10/schema"
    xmlns:wsrfr="http://docs.oasis-open.org/wsrf/r-2"
    xmlns:ns1="http://www.onvif.org/ver10/actionengine/wsdl"
    xmlns:tev="http://www.onvif.org/ver10/events/wsdl"
    xmlns:ns10="http://www.onvif.org/ver10/events/wsdl/PullPointBinding"
    xmlns:ns11="http://www.onvif.org/ver10/events/wsdl/CreatePullPointBinding"
    xmlns:ns12="http://www.onvif.org/ver10/events/wsdl/PausableSubscriptionManagerBinding"
    xmlns:ns13="http://www.onvif.org/ver10/network/wsdl/RemoteDiscoveryBinding"
    xmlns:ns14="http://www.onvif.org/ver10/network/wsdl/DiscoveryLookupBinding"
    xmlns:tdn="http://www.onvif.org/ver10/network/wsdl"
    xmlns:ns3="http://www.onvif.org/ver20/analytics/wsdl/RuleEngineBinding"
    xmlns:ns4="http://www.onvif.org/ver20/analytics/wsdl/AnalyticsEngineBinding"
    xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl"
    xmlns:ns5="http://www.onvif.org/ver10/events/wsdl/PullPointSubscriptionBinding"
    xmlns:ns6="http://www.onvif.org/ver10/events/wsdl/EventBinding"
    xmlns:ns7="http://www.onvif.org/ver10/events/wsdl/SubscriptionManagerBinding"
    xmlns:ns8="http://www.onvif.org/ver10/events/wsdl/NotificationProducerBinding"
    xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"
    xmlns:ns9="http://www.onvif.org/ver10/events/wsdl/NotificationConsumerBinding"
    xmlns:tad="http://www.onvif.org/ver10/analyticsdevice/wsdl"
    xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
    xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"
    xmlns:tls="http://www.onvif.org/ver10/display/wsdl"
    xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl"
    xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
    xmlns:trc="http://www.onvif.org/ver10/recording/wsdl"
    xmlns:trp="http://www.onvif.org/ver10/replay/wsdl"
    xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
    xmlns:trv="http://www.onvif.org/ver10/receiver/wsdl"
    xmlns:tse="http://www.onvif.org/ver10/search/wsdl" xmlns:ter="http://www.onvif.org/ver10/error"
    xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    wstop:topic="false">
    <MotionAlarm wstop:topic="true">
        <tt:MessageDescription IsProperty="true">
            <tt:Source>
                <tt:SimpleItemDescription Name="Source" Type="tt:ReferenceToken" />
            </tt:Source>
            <tt:Data>
                <tt:SimpleItemDescription Name="State" Type="xsd:boolean" />
            </tt:Data>
        </tt:MessageDescription>
    </MotionAlarm>
    <ImageTooDark wstop:topic="false">
        <ImagingService wstop:topic="true">
            <tt:MessageDescription IsProperty="true">
                <tt:Source>
                    <tt:SimpleItemDescription Name="Source" Type="tt:ReferenceToken" />
                </tt:Source>
                <tt:Data>
                    <tt:SimpleItemDescription Name="State" Type="xsd:boolean" />
                </tt:Data>
            </tt:MessageDescription>
        </ImagingService>
    </ImageTooDark>
</tns1:VideoSource>

<tns1:Media xmlns:tns1="http://www.onvif.org/ver10/topics"
    xmlns:wstop="http://docs.oasis-open.org/wsn/t-1"
    xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
    xmlns:SOAP-ENC="http://www.w3.org/2003/05/soap-encoding"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
    xmlns:wsdd="http://schemas.xmlsoap.org/ws/2005/04/discovery"
    xmlns:wsa5="http://www.w3.org/2005/08/addressing" xmlns:xmime="http://tempuri.org/xmime.xsd"
    xmlns:xmime5="http://www.w3.org/2005/05/xmlmime"
    xmlns:xop="http://www.w3.org/2004/08/xop/include"
    xmlns:wsrfbf="http://docs.oasis-open.org/wsrf/bf-2" xmlns:tt="http://www.onvif.org/ver10/schema"
    xmlns:wsrfr="http://docs.oasis-open.org/wsrf/r-2"
    xmlns:ns1="http://www.onvif.org/ver10/actionengine/wsdl"
    xmlns:tev="http://www.onvif.org/ver10/events/wsdl"
    xmlns:ns10="http://www.onvif.org/ver10/events/wsdl/PullPointBinding"
    xmlns:ns11="http://www.onvif.org/ver10/events/wsdl/CreatePullPointBinding"
    xmlns:ns12="http://www.onvif.org/ver10/events/wsdl/PausableSubscriptionManagerBinding"
    xmlns:ns13="http://www.onvif.org/ver10/network/wsdl/RemoteDiscoveryBinding"
    xmlns:ns14="http://www.onvif.org/ver10/network/wsdl/DiscoveryLookupBinding"
    xmlns:tdn="http://www.onvif.org/ver10/network/wsdl"
    xmlns:ns3="http://www.onvif.org/ver20/analytics/wsdl/RuleEngineBinding"
    xmlns:ns4="http://www.onvif.org/ver20/analytics/wsdl/AnalyticsEngineBinding"
    xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl"
    xmlns:ns5="http://www.onvif.org/ver10/events/wsdl/PullPointSubscriptionBinding"
    xmlns:ns6="http://www.onvif.org/ver10/events/wsdl/EventBinding"
    xmlns:ns7="http://www.onvif.org/ver10/events/wsdl/SubscriptionManagerBinding"
    xmlns:ns8="http://www.onvif.org/ver10/events/wsdl/NotificationProducerBinding"
    xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"
    xmlns:ns9="http://www.onvif.org/ver10/events/wsdl/NotificationConsumerBinding"
    xmlns:tad="http://www.onvif.org/ver10/analyticsdevice/wsdl"
    xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
    xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"
    xmlns:tls="http://www.onvif.org/ver10/display/wsdl"
    xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl"
    xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
    xmlns:trc="http://www.onvif.org/ver10/recording/wsdl"
    xmlns:trp="http://www.onvif.org/ver10/replay/wsdl"
    xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
    xmlns:trv="http://www.onvif.org/ver10/receiver/wsdl"
    xmlns:tse="http://www.onvif.org/ver10/search/wsdl" xmlns:ter="http://www.onvif.org/ver10/error"
    xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    wstop:topic="false">
    <ProfileChanged wstop:topic="true">
        <tt:MessageDescription IsProperty="false">
            <tt:Data>
                <tt:SimpleItemDescription Name="Token" Type="tt:ReferenceToken" />
            </tt:Data>
        </tt:MessageDescription>
    </ProfileChanged>
    <ConfigurationChanged wstop:topic="true">
        <tt:MessageDescription IsProperty="false">
            <tt:Source>
                <tt:SimpleItemDescription Name="Token" Type="tt:ReferenceToken" />
            </tt:Source>
            <tt:Data>
                <tt:SimpleItemDescription Name="Type" Type="xsd:string" />
            </tt:Data>
        </tt:MessageDescription>
    </ConfigurationChanged>
</tns1:Media>

<tns1:RuleEngine xmlns:tns1="http://www.onvif.org/ver10/topics"
    xmlns:wstop="http://docs.oasis-open.org/wsn/t-1"
    xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
    xmlns:SOAP-ENC="http://www.w3.org/2003/05/soap-encoding"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
    xmlns:wsdd="http://schemas.xmlsoap.org/ws/2005/04/discovery"
    xmlns:wsa5="http://www.w3.org/2005/08/addressing" xmlns:xmime="http://tempuri.org/xmime.xsd"
    xmlns:xmime5="http://www.w3.org/2005/05/xmlmime"
    xmlns:xop="http://www.w3.org/2004/08/xop/include"
    xmlns:wsrfbf="http://docs.oasis-open.org/wsrf/bf-2" xmlns:tt="http://www.onvif.org/ver10/schema"
    xmlns:wsrfr="http://docs.oasis-open.org/wsrf/r-2"
    xmlns:ns1="http://www.onvif.org/ver10/actionengine/wsdl"
    xmlns:tev="http://www.onvif.org/ver10/events/wsdl"
    xmlns:ns10="http://www.onvif.org/ver10/events/wsdl/PullPointBinding"
    xmlns:ns11="http://www.onvif.org/ver10/events/wsdl/CreatePullPointBinding"
    xmlns:ns12="http://www.onvif.org/ver10/events/wsdl/PausableSubscriptionManagerBinding"
    xmlns:ns13="http://www.onvif.org/ver10/network/wsdl/RemoteDiscoveryBinding"
    xmlns:ns14="http://www.onvif.org/ver10/network/wsdl/DiscoveryLookupBinding"
    xmlns:tdn="http://www.onvif.org/ver10/network/wsdl"
    xmlns:ns3="http://www.onvif.org/ver20/analytics/wsdl/RuleEngineBinding"
    xmlns:ns4="http://www.onvif.org/ver20/analytics/wsdl/AnalyticsEngineBinding"
    xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl"
    xmlns:ns5="http://www.onvif.org/ver10/events/wsdl/PullPointSubscriptionBinding"
    xmlns:ns6="http://www.onvif.org/ver10/events/wsdl/EventBinding"
    xmlns:ns7="http://www.onvif.org/ver10/events/wsdl/SubscriptionManagerBinding"
    xmlns:ns8="http://www.onvif.org/ver10/events/wsdl/NotificationProducerBinding"
    xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"
    xmlns:ns9="http://www.onvif.org/ver10/events/wsdl/NotificationConsumerBinding"
    xmlns:tad="http://www.onvif.org/ver10/analyticsdevice/wsdl"
    xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
    xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"
    xmlns:tls="http://www.onvif.org/ver10/display/wsdl"
    xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl"
    xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
    xmlns:trc="http://www.onvif.org/ver10/recording/wsdl"
    xmlns:trp="http://www.onvif.org/ver10/replay/wsdl"
    xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
    xmlns:trv="http://www.onvif.org/ver10/receiver/wsdl"
    xmlns:tse="http://www.onvif.org/ver10/search/wsdl" xmlns:ter="http://www.onvif.org/ver10/error"
    xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    wstop:topic="true">
    <CellMotionDetector wstop:topic="true">
        <Motion wstop:topic="true">
            <tt:MessageDescription IsProperty="true">
                <tt:Source>
                    <tt:SimpleItemDescription Name="VideoSourceConfigurationToken"
                        Type="tt:ReferenceToken" />
                    <tt:SimpleItemDescription Name="VideoAnalyticsConfigurationToken"
                        Type="tt:ReferenceToken" />
                    <tt:SimpleItemDescription Name="Rule" Type="xsd:string" />
                </tt:Source>
                <tt:Data>
                    <tt:SimpleItemDescription Name="IsMotion" Type="xsd:boolean" />
                </tt:Data>
            </tt:MessageDescription>
        </Motion>
    </CellMotionDetector>
</tns1:RuleEngine>