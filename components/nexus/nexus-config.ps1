@{
 Asset='nexus01_10.10.10.51_maven制品库'
 Service='nexus-webapp'
 Home='D:\nexus\nexus-2.14.11-01-bundle\nexus-2.14.11-01'
 Work='D:\nexus\nexus-2.14.11-01-bundle\sonatype-work\nexus'
 Primary='\\10.10.11.243\资源备份'
 Secondary='\\10.10.10.254\资源备份'
 Tertiary='\\192.168.5.240\资源备份'
 KeepDates=15
 # Live preseed is not a valid backup. Stopped final sync is capped below.
 MaxStoppedCopySeconds=240
 RestartWatchdogMinutes=8
 NotifyConfig='C:\core-backup\notify.json'
}
