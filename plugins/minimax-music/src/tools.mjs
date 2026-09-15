import {ACCESS_NOTICE, requireAccess} from './config.mjs';
import {startJob,getJob,listJobs,retryDownload} from './jobs.mjs';
import {validateArgs,buildRequest} from './requests.mjs';
const str=(description,extras={})=>({type:'string',description,...extras});
const common={output_dir:str('Absolute directory for user-facing output. A unique subdirectory is created.'),title:str('Local asset title; also sent to the lyrics API when applicable.',{maxLength:200}),request_id:str('Stable unique ID for this intentional generation. Reusing it returns the existing job; changed arguments are rejected.',{pattern:'^[A-Za-z0-9_-]{1,100}$'}),backend:{type:'string',enum:['cloud','local'],description:'Use configured backend unless explicitly overriding. No automatic fallback.'}};
const audio={stream:{type:'boolean',description:'Cloud SSE streaming, saved locally while generating.'},output_format:{type:'string',enum:['hex','url'],description:'Cloud transport; streaming requires hex. URL results are downloaded automatically.'},audio_setting:{type:'object',additionalProperties:false,properties:{format:{type:'string',enum:['wav','mp3','pcm']},sample_rate:{type:'integer',enum:[16000,24000,32000,44100]},bitrate:{type:'integer',enum:[32000,64000,128000,256000]}}},aigc_watermark:{type:'boolean',description:'Append MiniMax audio watermark. Non-streaming cloud only.'}};
const generation={prompt:str('Music style, emotion, vocal delivery, instruments and section development. Cloud maximum 2000 characters.'),model:{type:'string',enum:['music-3.0','music-2.6']},duration_seconds:{type:'integer',minimum:1,maximum:300,description:'Self-hosted ONLY. Maximum duration, not guaranteed exact length. Cloud has no duration parameter.'},seed:{type:'integer',minimum:0,maximum:2147483647,description:'Self-hosted ONLY.'},...audio};
const reference={audio_url:str('Public HTTPS reference audio URL; 6..360 seconds, <=50 MB.'),audio_path:str('Absolute local reference audio path. Its bytes are sent to MiniMax; use only a file the user selected.'),audio_base64:str('Base64 reference audio, <=50 MB decoded. Prefer audio_path for local files.')};
const writeAnnotations={readOnlyHint:false,destructiveHint:false,idempotentHint:false,openWorldHint:true};
const readAnnotations={readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:false};
function tool(name,description,properties,required=[],annotations=writeAnnotations) {return {name,description,inputSchema:{type:'object',properties,required,additionalProperties:false},annotations};}
export const TOOLS=[
  tool('music_capabilities','Read supported MiniMax music modes, configuration readiness and current access restrictions. No API call or charge.',{},[],readAnnotations),
  tool('generate_song','Start an original vocal song from lyrics or a prompt with lyrics_optimizer. May incur a MiniMax charge. Returns job_id; poll get_music_job.',{...common,...generation,lyrics:str('Lyrics with section tags such as [Verse] and [Chorus], up to 3500 characters.'),lyrics_optimizer:{type:'boolean',description:'Cloud: auto-write lyrics when lyrics are empty. Self-hosted requires supplied lyrics.'}},['output_dir']),
  tool('generate_instrumental','Start instrumental BGM or a soundtrack. No vocals. May incur a MiniMax charge. Returns job_id.',{...common,...generation},['output_dir','prompt']),
  tool('write_lyrics','Write complete structured lyrics and a song title/style tags through MiniMax. Cloud API, may incur a charge.',{...common,prompt:str('Theme, language and writing direction. Empty means random.',{maxLength:2000})},['output_dir']),
  tool('edit_lyrics','Edit or continue existing lyrics through MiniMax. Describe which sections to retain/change. Cloud API, may incur a charge.',{...common,prompt:str('Editing or continuation instructions.',{maxLength:2000}),lyrics:str('Existing lyrics.',{minLength:1,maxLength:3500})},['output_dir','lyrics']),
  tool('preprocess_cover','Analyze a reference song into editable structured lyrics, section timestamps and a cover_feature_id valid for 24 hours. Sends reference audio to MiniMax.',{...common,...reference},['output_dir']),
  tool('generate_cover','Generate a restyled cover, optionally changing lyrics. Supply ONE reference source or cover_feature_id. Feature IDs require edited lyrics. May incur a charge.',{...common,...reference,...audio,cover_feature_id:str('Feature ID from preprocess_cover, valid for 24 hours.'),prompt:str('Target cover style.',{minLength:10,maxLength:300}),lyrics:str('Optional revised lyrics; required with cover_feature_id.',{minLength:10,maxLength:1000})},['output_dir','prompt']),
  tool('get_music_job','Read a background job and its saved output paths. completed means audio/lyrics exist; unknown must not be automatically resubmitted.',{job_id:str('Job ID returned by a generation tool.')},['job_id'],readAnnotations),
  tool('list_music_jobs','List recent local music jobs; no API request.',{limit:{type:'integer',minimum:1,maximum:100}},[],readAnnotations),
  tool('download_result','Retry downloading an already-generated audio URL after download_failed. Does not submit or charge for another generation.',{job_id:str('A download_failed job ID.')},['job_id'],{...writeAnnotations,idempotentHint:true}),
  tool('preview_music_request','Validate and preview a request without credentials, network calls, or generation. Reference bytes are omitted.',{operation:{type:'string',enum:['song','instrumental','lyrics_write','lyrics_edit','cover_preprocess','cover']},arguments:{type:'object',description:'The same arguments as the corresponding generation tool.'}},['operation','arguments'],readAnnotations)
];
export const OPERATIONS_BY_TOOL={generate_song:'song',generate_instrumental:'instrumental',write_lyrics:'lyrics_write',edit_lyrics:'lyrics_edit',preprocess_cover:'cover_preprocess',generate_cover:'cover'};
export async function invoke(name,args,cfg,entrypoint) {
  if(!args||typeof args!=='object'||Array.isArray(args)) throw new Error('Arguments must be an object');
  const definition=TOOLS.find(t=>t.name===name);
  if(!definition) throw new Error('Unknown tool: '+name);
  for(const k of Object.keys(args)) if(!Object.hasOwn(definition.inputSchema.properties,k)) throw new Error('Unknown argument: '+k);
  for(const k of definition.inputSchema.required) if(args[k]===undefined) throw new Error('Missing argument: '+k);
  if(OPERATIONS_BY_TOOL[name]) return startJob(OPERATIONS_BY_TOOL[name],args,cfg,entrypoint);
  if(name==='get_music_job') return getJob(cfg,args.job_id);
  if(name==='list_music_jobs') return listJobs(cfg,args.limit);
  if(name==='download_result') return retryDownload(cfg,args.job_id);
  if(name==='preview_music_request') {
    const backend=validateArgs(args.operation,args.arguments,cfg);
    const request=buildRequest(args.operation,args.arguments,backend);
    if(request.body.audio_base64) request.body.audio_base64='[reference audio omitted from preview]';
    return {backend,...request,network_request_made:false};
  }
  if(name==='music_capabilities') return {
    version:'1.0.0',checked_at:'2026-09-15',configured_backend:cfg.backend,cloud_key_configured:Boolean(cfg.key),api_access_verified:false,config_path:cfg.configFile,data_dir:cfg.dataDir,
    cloud:{region:cfg.region,base_url:cfg.apiBase,models:['music-3.0','music-2.6','music-cover'],modes:['song','instrumental','lyrics_write','lyrics_edit','cover_preprocess','cover'],formats:['wav','mp3','pcm'],streaming:true,exact_duration:false,seed:false},
    local:{base_url:cfg.localBase,model:'MiniMaxAI/MiniMax-Music3',adapter:'SGLang-Omni /v1/audio/speech',modes:['song','instrumental'],format:'wav',sample_rate:32000,duration_limit_seconds:300,streaming:false,requires_running_server:true},
    access_notice:ACCESS_NOTICE,web_url:'https://www.minimax.cn/audio/music',limitations:['No guaranteed frame-accurate cue timing, melody preservation, stems, voice cloning, or seamless loops.','No automatic cloud-to-local fallback or paid POST retries.','Cloud URL results and cover features expire after approximately 24 hours.','Reference duration (6..360 seconds) is checked by MiniMax; the plugin validates local byte size.'],tools:TOOLS.map(t=>t.name)
  };
}
