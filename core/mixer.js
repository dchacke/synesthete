// Synesthete's player on macOS. Unlike afplay, it can fade out sounds that
// have already started. It takes commands on stdin, one per line:
//
//   play <delay> <path>  play the WAV at <path>, <delay> seconds from now
//   fade <seconds>       fade out everything playing or due, over <seconds>
//   stop                 silence everything at once, and exit
//
// A sound that is fading carries on fading as it was. When stdin closes,
// because Sublime quit, the rest fade out over the seconds given as the
// argument, and the mixer exits once everything is quiet.

ObjC.import('AVFoundation');

var input = $.NSFileHandle.fileHandleWithStandardInput;
var sounds = [];  // {player, ends, fading: {from, seconds}}, in ms
var pending = '';
var open = true;
var stopped = false;

function play(delay, path) {
  var url = $.NSURL.fileURLWithPath(path);
  var player = $.AVAudioPlayer.alloc.initWithContentsOfURLError(url, null);
  if (player.isNil()) {
    return;
  }
  if (delay > 0) {
    player.playAtTime(Number(player.deviceCurrentTime) + delay);
  } else {
    player.play;
  }
  var ends = Date.now() + 1000 * (delay + Number(player.duration));
  sounds.push({player: player, ends: ends, fading: null});
}

function fade(seconds) {
  var now = Date.now();
  sounds.forEach(function (sound) {
    if (!sound.fading) {
      sound.fading = {from: now, seconds: seconds};
    }
  });
}

function stop() {
  sounds.forEach(function (sound) {
    sound.player.stop;
  });
  sounds = [];
  stopped = true;
}

function handle(line) {
  var words = line.split(' ');
  if (words[0] === 'play') {
    play(parseFloat(words[1]), words.slice(2).join(' '));
  } else if (words[0] === 'fade') {
    fade(parseFloat(words[1]));
  } else if (words[0] === 'stop') {
    stop();
  }
}

// Turn down the sounds that are fading, the way music.fade_file does, and
// let go of the ones that are over.
function tick() {
  var now = Date.now();
  sounds = sounds.filter(function (sound) {
    var over = now > sound.ends;
    if (sound.fading && !over) {
      var x = (now - sound.fading.from) / (1000 * sound.fading.seconds);
      over = x >= 1;
      sound.player.volume = Math.pow(Math.max(0, 1 - x), 2);
    }
    if (over) {
      sound.player.stop;
    }
    return !over;
  });
}

ObjC.registerSubclass({
  name: 'SynestheteMixerInput',
  methods: {
    'read:': {
      types: ['void', ['id']],
      implementation: function (notification) {
        var data = input.availableData;
        if (Number(data.length) === 0) {
          open = false;
          return;
        }
        input.waitForDataInBackgroundAndNotify;
        pending += $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding).js;
        var lines = pending.split('\n');
        pending = lines.pop();
        lines.forEach(function (line) {
          try {
            handle(line);
          } catch (e) {
            // A sound that won't play shouldn't keep the rest from playing.
          }
        });
      }
    }
  }
});

// Notifications don't keep their observer alive, so this does.
var reader = $.SynestheteMixerInput.alloc.init;

function run(argv) {
  var quitSeconds = parseFloat(argv[0]) || 0;
  $.NSNotificationCenter.defaultCenter.addObserverSelectorNameObject(
    reader, 'read:', $.NSFileHandleDataAvailableNotification, input);
  input.waitForDataInBackgroundAndNotify;
  $.NSFileHandle.fileHandleWithStandardOutput.writeData(
    $('ready\n').dataUsingEncoding($.NSUTF8StringEncoding));
  while (!stopped && (open || sounds.length > 0)) {
    var fading = sounds.some(function (sound) { return sound.fading; });
    $.NSRunLoop.currentRunLoop.runModeBeforeDate(
      $.NSDefaultRunLoopMode, $.NSDate.dateWithTimeIntervalSinceNow(fading ? 0.02 : 0.5));
    if (!open) {
      fade(quitSeconds);
    }
    tick();
  }
}
