import { sceneSchema } from './model.mjs';
const path=d=>({type:'path',d});
const part=(id,name,kind,d,z=0)=>({id,name,kind,elements:[path(d)],z});
export const templateNames=['shirt','jacket','dress','trousers','custom'];
export function template(name,title,brief=''){
  let parts;
  if(name==='shirt'||name==='jacket'){
    parts=[
      part('sleeve-left','左袖','sleeve','M260 190 L200 220 L100 650 L170 680 L280 390 Z'),
      part('sleeve-right','右袖','sleeve','M540 190 L600 220 L700 650 L630 680 L520 390 Z'),
      part('body-front','衣身正面','body','M260 190 L340 145 Q400 180 460 145 L540 190 L540 790 Q400 825 260 790 Z',1),
      part('closure','门襟','closure','M390 180 L410 180 L410 805 L390 805 Z',2),
      part('collar','领口','collar','M340 145 L395 195 L350 260 L305 180 Z M460 145 L405 195 L450 260 L495 180 Z',4),
      part('pocket','胸袋','pocket','M445 330 L515 330 L515 405 Q480 435 445 405 Z',3),
      {...part('buttons','纽扣','decoration','M400 280 L400 750',3),fill:'#333333',elements:[280,380,480,580,680,755].map(cy=>({type:'ellipse',cx:400,cy,rx:4,ry:4}))},
      {...part('body-back','衣身背面','body','M260 190 L340 145 Q400 180 460 145 L540 190 L540 790 Q400 825 260 790 Z',1),view:'back'},
      {...part('sleeve-back-left','背面左袖','sleeve','M260 190 L200 220 L100 650 L170 680 L280 390 Z'),view:'back'},
      {...part('sleeve-back-right','背面右袖','sleeve','M540 190 L600 220 L700 650 L630 680 L520 390 Z'),view:'back'},
    ];
    if(name==='jacket') parts.push(part('pocket-lower','下侧口袋','pocket','M290 560 L365 560 L365 690 L290 690 Z',3));
  } else if(name==='dress'){
    parts=[part('body-front','连衣裙正面','body','M290 135 L350 105 Q400 160 450 105 L510 135 L480 310 L450 420 L570 880 Q400 925 230 880 L350 420 L320 310 Z'),
      part('waist','腰部','decoration','M350 412 L450 412 L454 440 L346 440 Z',2),
      {...part('body-back','连衣裙背面','body','M290 135 L350 105 Q400 135 450 105 L510 135 L480 310 L450 420 L570 880 Q400 925 230 880 L350 420 L320 310 Z'),view:'back'}];
  } else if(name==='trousers'){
    const outline='M255 130 L545 130 L550 430 L530 880 L415 880 L400 460 L385 880 L270 880 L250 430 Z';
    parts=[part('body-front','裤身正面','body',outline),part('waist','腰头','hem','M255 130 L545 130 L545 170 L255 170 Z',2),
      {...part('body-back','裤身背面','body',outline),view:'back'},part('pockets','斜插袋','pocket','M255 175 L335 190 L285 280 M545 175 L465 190 L515 280',2)];
    parts.at(-1).fill='none';
  }else if(name==='custom')throw new Error('Custom designs require a scene with your own vector parts.');
  else throw new Error('Unknown template.');
  return sceneSchema.parse({title,brief,parts});
}
