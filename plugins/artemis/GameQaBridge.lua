-- Fixed-format input state machine; callbacks are activity counters, not game frames.
local M = {}
local State = {}
State.__index = State
local MOD = 2147483648
local function remaining(deadline, now) return (deadline - now) % MOD end
local function integer(text)
    local n = tonumber(text)
    if not n or n < 0 or n >= MOD or n ~= math.floor(n) or tostring(n) ~= text then return nil end
    return n
end
function M.new(session, pid)
    assert(type(session)=='string' and #session==32 and session:match('^[a-f0-9]+$'))
    assert(type(pid)=='number' and pid>0 and pid<MOD and pid==math.floor(pid))
    return setmetatable({session=session,pid=pid,seq=0,kind=0,hold=0,ack_seq=0,ack_kind=0,
        paused=true,held=false,desired=false,closed=false,reason='none'},State)
end
function State:fail(reason)
    self.paused,self.desired,self.pending,self.reason = true,false,false,reason
end
function State:update(raw, now)
    if raw==nil and self.seq==0 then return end
    if type(raw)~='string' or #raw>512 then self:fail('invalid'); return end
    local token,pid,seq,kind,lease,hold=raw:match('^GQA1 ([a-f0-9]+) (%d+) (%d+) (%d+) (%d+) (%d+)\n$')
    pid,seq,kind,lease,hold=integer(pid),integer(seq),integer(kind),integer(lease),integer(hold)
    if token~=self.session or pid~=self.pid or not seq or seq<1 or not kind or kind<1 or kind>5
        or not lease or not hold or seq<self.seq or (kind~=2 and hold~=0) then self:fail('invalid'); return end
    local lease_left=remaining(lease,now)
    if lease_left>1500 and lease_left<MOD/2 then self:fail('invalid'); return end
    if kind==2 then
        local left=remaining(hold,now)
        if left>1000 and left<MOD/2 then self:fail('invalid'); return end
    end
    if seq==self.seq then
        if kind~=self.kind or hold~=self.hold then self:fail('invalid'); return end
    else
        self.seq,self.kind,self.hold,self.pending=seq,kind,hold,true
        if kind==1 then
            if self.closed then self:fail('invalid'); return end
            self.paused,self.desired,self.reason=false,false,'none'
        elseif kind==2 then
            if self.paused or self.closed then self:fail('invalid'); return end
            self.reason='none'
        else
            self.desired,self.reason=false,'none'
            if kind==4 or kind==5 then self.paused=true end
            if kind==5 then self.closed=true end
        end
    end
    if kind<=2 and (lease_left==0 or lease_left>1500) then self:fail('lease_expired'); return end
    if kind==2 and not self.paused then
        local left=remaining(self.hold,now)
        self.desired=left>0 and left<=1000
        if not self.desired then self.reason='action_expired' end
    end
end
function State:consumed(down, sample, now)
    self.held=down
    local expected=self.kind==2 and self.desired and not self.paused
    if self.pending and down==expected then
        self.ack_seq,self.ack_kind,self.pending=self.seq,self.kind,false
    end
    return string.format('GQS1 %s %d %d %d %d %d %d %d %d %d %s\n',
        self.session,self.pid,sample,self.callbacks or 0,now,self.seq,self.ack_seq,self.ack_kind,
        self.paused and 1 or 0,down and 1 or 0,self.reason)
end
function M.readCommand(state)
    local generation=state.generation or 0
    local path='input.'..generation..'.txt'
    local f=io.open(path,'rb')
    if not f then return nil end
    local raw=f:read(513); f:close()
    if not raw or #raw>512 then return raw end
    local nextFile,err,code=io.open('input.'..(generation+1)..'.txt','rb')
    if nextFile then
        raw=nextFile:read(513); nextFile:close()
        state.generation=generation+1
        if generation>=1 then os.remove('input.'..(generation-1)..'.txt') end
    -- A new name can briefly deny opens while Windows completes publication. The
    -- current immutable record stays authoritative, including its original deadlines.
    elseif code~=2 and code~=13 then
        return nil
    end
    return raw
end
return M
