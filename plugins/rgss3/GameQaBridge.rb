# RGSS3 实验桥：仅独立桌面，单帧输入；不猜测原生长按重复时序。
if ENV['GAMEQAKIT_BRIDGE_DIR'] && ENV['GAMEQAKIT_SESSION_ID']
  module GameQaBridge
    KEYS = {'up'=>:UP, 'down'=>:DOWN, 'left'=>:LEFT, 'right'=>:RIGHT,
            'ok'=>:C, 'cancel'=>:B, 'shift'=>:A, 'pageup'=>:L, 'pagedown'=>:R}
    class << self
      def read_ini(name)
        path=File.join(@root,name+'.ini')
        retries=0
        begin
          body=File.open(path,'rb') {|f|f.read(131073)}
        rescue Errno::ENOENT
          return {}
        rescue Errno::EACCES
          retries+=1
          raise if retries>20
          sleep(0.001)
          retry
        end
        raise 'oversized_control_file' if body.bytesize>131072
        result={}
        body.force_encoding('UTF-8').each_line do |line|
          next if line.strip.empty? || line =~ /^\s*[;#\[]/
          key,value=line.split('=',2)
          result[key.strip]=value.strip if value
        end
        result
      end
      def json(value)
        case value
        when Hash then '{'+value.map {|k,v|json(k.to_s)+':'+json(v)}.join(',')+'}'
        when Array then '['+value.map {|v|json(v)}.join(',')+']'
        when String
          '"'+value.gsub(/["\\\x00-\x1f]/) {|c|c=='"' ? '\\"' : c=='\\' ? '\\\\' : '\\u%04x'%c.ord}+'"'
        when Numeric then value.to_s
        when TrueClass then 'true'
        when FalseClass then 'false'
        when NilClass then 'null'
        else json(value.to_s)
        end
      end
      def write(name,value)
        path=File.join(@root,name)
        File.open(path+'.part','wb') {|f|f.write(json(value))}
        File.rename(path+'.part',path)
        true
      rescue Errno::EACCES, Errno::EEXIST
        false
      end
      def start
        @root=ENV['GAMEQAKIT_BRIDGE_DIR']
        @meta=read_ini('connection')
        pid=Win32API.new('kernel32','GetCurrentProcessId','','l').call
        return false unless @meta['protocol']=='1' && @meta['session_id']==ENV['GAMEQAKIT_SESSION_ID'] && @meta['pid'].to_i==pid
        desktop=Win32API.new('user32','GetThreadDesktop','l','l').call(Win32API.new('kernel32','GetCurrentThreadId','','l').call)
        buffer="\0"*256; needed=[0].pack('L')
        ok=Win32API.new('user32','GetUserObjectInformationA','llplp','i').call(desktop,2,buffer,256,needed)
        @isolated=ok!=0 && buffer.split("\0")[0]==ENV['GAMEQAKIT_DESKTOP_NAME'] && buffer.index('gameqakit-')==0
        ['results','screenshots'].each {|name|Dir.mkdir(File.join(@root,name)) unless File.directory?(File.join(@root,name))}
        @paused=true; @held={}; @current=nil; @last_request=''; @accepted_pause=''; @observed_pause=''; @last_status=0
        @error=nil; @ready=false; @frame=0
        status(true)
        true
      end
      def scene
        defined?(SceneManager) && SceneManager.respond_to?(:scene) ? SceneManager.scene : nil
      end
      def in_input_handler
        @handler_depth=(@handler_depth || 0)+1
        yield
      ensure
        @handler_depth-=1
      end
      def ready?
        return false if (@handler_depth || 0)>0
        current_scene=scene
        return false unless current_scene && Graphics.brightness==255
        return false if current_scene.respond_to?(:scene_changing?) && current_scene.scene_changing?
        if current_scene.class.to_s=='Scene_Map' && defined?($game_player) && $game_player
          return false if $game_player.moving?
        end
        current_scene.instance_variables.each do |name|
          window=current_scene.instance_variable_get(name)
          if window.respond_to?(:index) && window.respond_to?(:active) && window.active && window.respond_to?(:open?)
            return false unless window.open?
          end
        end
        true
      end
      def view
        player=nil
        if defined?($game_player) && $game_player && $game_player.respond_to?(:x)
          player={'x'=>$game_player.x,'y'=>$game_player.y,'direction'=>$game_player.direction,
                  'map_id'=>defined?($game_map) && $game_map ? $game_map.map_id : nil}
          if $game_player.respond_to?(:passable?) && defined?($game_map) && $game_map && $game_map.map_id>0
            player['passable']={}
            {'up'=>8,'down'=>2,'left'=>4,'right'=>6}.each do |name,direction|
              player['passable'][name]=$game_player.passable?($game_player.x,$game_player.y,direction)
            end
          end
        end
        {'scene'=>scene ? scene.class.to_s : nil,'input_ready'=>ready?,'player'=>player}
      end
      def status(force=false)
        return if !force && Time.now.to_f-@last_status<0.2
        value={'protocol'=>1,'session_id'=>@meta['session_id'],'pid'=>@meta['pid'].to_i,'exe_path'=>@meta['exe_path'],
               'engine'=>'rpgmaker_vxace','updated_at'=>Time.now.to_f,'frame'=>@frame,'paused'=>@paused,
               'capabilities'=>@isolated && @ready ? ['move','confirm','cancel','wait','release'] : [],'max_input_frames'=>1,
               'buttons'=>KEYS.keys,'screen'=>[Graphics.width,Graphics.height],'held'=>@held.keys,'input_isolation'=>@isolated ? 'private_desktop' : 'unverified',
               'command_id'=>@current ? @current['request']['id'] : nil,'error'=>@error}.merge(view)
        @last_status=Time.now.to_f if write('status.json',value)
      end
      def release
        @held={}
        Input.gameqa_native_update if Input.respond_to?(:gameqa_native_update)
      end
      def fail(message,request=nil)
        request||=@current && @current['request']
        consumed=@current && @current['consumed']
        @paused=true; release; @current=nil; @error=message.to_s
        if request && request['id'] =~ /\A[A-Za-z0-9_-]{1,64}\z/
          write('results/'+request['id']+'.json',{'protocol'=>1,'session_id'=>@meta['session_id'],'id'=>request['id'],
            'ok'=>false,'consumed'=>!!consumed,'frame'=>@frame,'paused'=>true,'error'=>@error})
        end
        status(true)
      end
      def lease_valid?
        lease=read_ini('lease')
        lease['session_id']==@meta['session_id'] && lease['expires_at'].to_f>=Time.now.to_f
      end
      def pause_id
        value=read_ini('pause')
        value['session_id']==@meta['session_id'] ? (value['id'] || '') : ''
      end
      def guard
        stop=pause_id
        if stop!=@observed_pause
          @observed_pause=stop
          fail('manual_pause') if !@paused || @current
          @paused=true; release
        end
        fail('lease_expired') if !@paused && !lease_valid?
      end
      def poll
        return if @current
        request=read_ini('request')
        id=request['id']
        return unless id && id!=@last_request && id =~ /\A[A-Za-z0-9_-]{1,64}\z/
        return unless request['protocol']=='1' && request['session_id']==@meta['session_id']
        if File.file?(File.join(@root,'results',id+'.json')); @last_request=id; return; end
        action=request['payload.action']; frames=request['payload.frames'].to_i
        valid=['move','confirm','cancel','wait','release','resume'].include?(action) && request['payload.frames'] =~ /\A\d+\z/ && frames.between?(1,120)
        fields=['payload.action','payload.frames']
        fields << 'payload.direction' if action=='move'
        fields << 'payload.button' if action=='release'
        valid &&= request.keys.grep(/\Apayload\./).all? {|key|fields.include?(key)}
        valid &&= !request['payload.button'] || KEYS.has_key?(request['payload.button'])
        valid &&= action=='wait' || action=='release' || action=='resume' || frames==1
        valid &&= ['up','down','left','right'].include?(request['payload.direction']) if action=='move'
        valid &&= request['after_frame'] =~ /\A\d+\z/ && request['after_frame'].to_i<=@frame
        unless valid; @last_request=id; fail('invalid_or_unsupported_command',request); return; end
        rejection = !@isolated ? 'isolated_desktop_required' : !lease_valid? ? 'fresh_lease_required' : (request['pause_id'] || '')!=pause_id ? 'pause_generation_changed' : nil
        if rejection
          @last_request=id; fail(rejection,request); return
        end
        return if !['resume','wait','release'].include?(action) && !ready?
        @last_request=id
        if action=='resume'
          @accepted_pause=request['pause_id'] || ''; @paused=false; @error=nil; release
        elsif @paused || (request['pause_id'] || '')!=@accepted_pause
          fail('control_paused',request); return
        end
        key=action=='move' ? request['payload.direction'] : action=='confirm' ? 'ok' : action=='cancel' ? 'cancel' : nil
        @held=key ? {key=>0} : {}
        @current={'request'=>request,'remaining'=>frames,'phase'=>'input','settle'=>2,'consumed'=>false}
      end
      def before_input
        guard; poll
        @held.keys.each {|key|@held[key]+=1}
        if @current && ['wait','resume','release'].include?(@current['request']['payload.action'])
          @current['consumed']=true
        end
      rescue StandardError => error
        write('plugin-error.json',{'error'=>error.message,'backtrace'=>error.backtrace})
        fail(error.message)
      end
      def ai?
        !@paused
      end
      def key_name(key)
        KEYS.each do |name,symbol|
          return name if key==symbol || (Input.const_defined?(symbol) && key==Input.const_get(symbol))
        end
        nil
      end
      def pressed?(key)
        name=key_name(key)
        pressed=name && @held[name] && @held[name]>0
        @current['consumed']=true if pressed && @current
        !!pressed
      end
      def direction
        dx=(@held['right'] ? 1 : 0)-(@held['left'] ? 1 : 0)
        dy=(@held['down'] ? 1 : 0)-(@held['up'] ? 1 : 0)
        value=dx!=0 ? (dx>0 ? 6 : 4) : dy!=0 ? (dy>0 ? 2 : 8) : 0
        @current['consumed']=true if value!=0 && @current
        value
      end
      def capture(id)
        bitmap=Graphics.snap_to_bitmap
        width=bitmap.width; height=bitmap.height; stride=(width*3+3)/4*4
        relative='screenshots/'+id+'.bmp'; target=File.join(@root,relative)
        File.open(target+'.part','wb') do |f|
          f.write('BM'+[54+stride*height,0,54].pack('V3'))
          f.write([40,width,-height,1,24,0,stride*height,0,0,0,0].pack('VllvvVVllVV'))
          height.times do |y|
            pixels=[]
            width.times do |x|
              color=bitmap.get_pixel(x,y); pixels << color.blue.to_i << color.green.to_i << color.red.to_i
            end
            f.write(pixels.pack('C*')); f.write("\0"*(stride-width*3))
          end
        end
        File.rename(target+'.part',target)
        relative
      ensure
        bitmap.dispose if bitmap
      end
      def after_render
        @frame+=1
        @ready=true
        guard
        if @current
          if @current['phase']=='input'
            @current['remaining']-=1
            if @current['remaining']<=0; release; @current['phase']='settle'; end
          elsif @current['phase']=='settle'
            @current['settle']-=1
            if @current['settle']<=0 && ready?
              request=@current['request']
              unless @current['consumed']; fail('input_not_consumed',request); return; end
              screenshot=capture(request['id'])
              value={'protocol'=>1,'session_id'=>@meta['session_id'],'id'=>request['id'],'ok'=>true,'consumed'=>true,
                     'frame'=>@frame,'capture_frame'=>@frame,'screenshot'=>screenshot,'paused'=>@paused}.merge(view)
              @current=nil if write('results/'+request['id']+'.json',value)
              status(true)
            end
          end
        end
        status
      rescue StandardError => error
        write('plugin-error.json',{'error'=>error.message,'backtrace'=>error.backtrace})
        fail(error.message)
      end
    end
  end
  if GameQaBridge.start
    if defined?(Window_Selectable)
      class Window_Selectable
        alias_method :gameqa_native_process_handling, :process_handling
        def process_handling
          GameQaBridge.in_input_handler { gameqa_native_process_handling }
        end
      end
    end
    class << Input
      alias_method :gameqa_native_update, :update
      alias_method :gameqa_native_pressed, :press?
      alias_method :gameqa_native_triggered, :trigger?
      alias_method :gameqa_native_repeated, :repeat?
      alias_method :gameqa_native_dir4, :dir4
      alias_method :gameqa_native_dir8, :dir8
      def update
        GameQaBridge.before_input
        gameqa_native_update unless GameQaBridge.ai?
      end
      def press?(key); GameQaBridge.ai? ? GameQaBridge.pressed?(key) : gameqa_native_pressed(key); end
      def trigger?(key); GameQaBridge.ai? ? GameQaBridge.pressed?(key) : gameqa_native_triggered(key); end
      def repeat?(key); GameQaBridge.ai? ? GameQaBridge.pressed?(key) : gameqa_native_repeated(key); end
      def dir4; GameQaBridge.ai? ? GameQaBridge.direction : gameqa_native_dir4; end
      def dir8; GameQaBridge.ai? ? GameQaBridge.direction : gameqa_native_dir8; end
    end
    class << Graphics
      alias_method :gameqa_native_update, :update
      def update
        gameqa_native_update
        GameQaBridge.after_render
      end
    end
  end
end
