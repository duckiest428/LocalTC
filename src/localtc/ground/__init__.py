"""The ground crew: the turnaround and the push by voice, on the ramp's frequency or the intercom.

Pure where it can be, as the ATC is: ``crew.RampCrew`` is told what's heard and what the services are doing and says what
to do, ``menu`` drives GSX's menu, ``grammar`` reads the pilot. ``service.GroundService`` does the waiting. What does the
work is behind ``api.GroundAdapter``: GSX through its documented Remote API (``gsx_remote``), the sim's own services
without it (``standalone``), or a stand-in for working without a sim (``mock``).
"""
