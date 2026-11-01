#import <Cocoa/Cocoa.h>

int main(void) {
    @autoreleasepool {
        [NSApplication.sharedApplication setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NSString *root = [NSBundle.mainBundle.bundlePath stringByDeletingLastPathComponent];
        NSString *data = [root stringByAppendingPathComponent:@"data"];
        NSError *error = nil;
        BOOL ready = [NSFileManager.defaultManager createDirectoryAtPath:data
            withIntermediateDirectories:YES attributes:nil error:&error];
        NSString *logPath = [data stringByAppendingPathComponent:@"launcher.log"];
        NSFileHandle *log = nil;
        if (ready) {
            [NSFileManager.defaultManager createFileAtPath:logPath contents:NSData.data attributes:nil];
            log = [NSFileHandle fileHandleForWritingAtPath:logPath];
        }
        NSTask *task = [[NSTask alloc] init];
        task.executableURL = [NSURL fileURLWithPath:@"/bin/bash"];
        task.arguments = @[[root stringByAppendingPathComponent:@"start_mac.command"]];
        task.currentDirectoryURL = [NSURL fileURLWithPath:root];
        task.standardInput = NSFileHandle.fileHandleWithNullDevice;
        if (log) {
            task.standardOutput = log;
            task.standardError = log;
        }
        if (ready && log && [task launchAndReturnError:&error]) {
            [task waitUntilExit];
            [log closeFile];
            if (task.terminationStatus == 0) return 0;
        }
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"Agent Watch 启动失败";
        alert.informativeText = error.localizedDescription ?:
            @"请查看项目 data/launcher.log 和 data/tray.err.log。";
        [alert runModal];
        return 1;
    }
}
